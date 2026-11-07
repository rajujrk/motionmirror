package com.motionmirror.jobs;

import com.motionmirror.audit.AuditService;
import com.motionmirror.config.AppProperties;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.shared.ApiException;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class JobService {

    public record Submission(UUID projectId, JobKind kind, String configRevision, String requestedSettings,
            String effectiveSettings, String inputs, String idempotencyKey, boolean consentConfirmed) {}

    private final JobStore jobs;
    private final OutboxStore outbox;
    private final JobRouting routing;
    private final AuditService audit;
    private final AppProperties properties;

    public JobService(JobStore jobs, OutboxStore outbox, JobRouting routing, AuditService audit, AppProperties properties) {
        this.jobs = jobs;
        this.outbox = outbox;
        this.routing = routing;
        this.audit = audit;
        this.properties = properties;
    }

    /** The job and its dispatch event commit together. The hard quota lives here, not in Redis. */
    @Transactional
    public JobRow submit(UUID ownerId, Submission submission) {
        if (submission.idempotencyKey() != null) {
            var existing = jobs.findByIdempotencyKey(ownerId, submission.idempotencyKey());
            if (existing.isPresent()) {
                return existing.get();
            }
        }
        if (submission.kind() != JobKind.VALIDATE) {
            jobs.lockOwnerQuota(ownerId);
            if (jobs.countActive(ownerId) >= properties.limits().maxActiveJobsPerUser()) {
                throw ApiException.tooMany("JOB_QUOTA_REACHED", "You already have the maximum number of active jobs");
            }
        }
        JobRouting.Route route = routing.routeFor(submission.kind());
        UUID id = UUID.randomUUID();
        try {
            jobs.insert(new JobStore.NewJob(id, submission.projectId(), ownerId, submission.kind(), route.backendId(),
                    route.routingKey(), submission.configRevision(), submission.requestedSettings(),
                    submission.effectiveSettings(), submission.inputs(), submission.idempotencyKey(),
                    properties.jobs().maxAttempts(), submission.consentConfirmed()));
        } catch (DuplicateKeyException raced) {
            return jobs.findByIdempotencyKey(ownerId, submission.idempotencyKey()).orElseThrow(() -> raced);
        }
        UUID eventId = outbox.enqueue(id, 1, submission.kind(), route.backendId(), submission.configRevision(), route.routingKey());
        jobs.addEvent(id, "QUEUED", Map.of("event_id", eventId.toString(), "dispatch_generation", 1));
        return jobs.find(id).orElseThrow();
    }

    @Transactional(readOnly = true)
    public JobRow requireOwned(CurrentUser user, UUID jobId) {
        return jobs.findOwned(jobId, user.id()).orElseThrow(() -> ApiException.notFound("Job"));
    }

    @Transactional(readOnly = true)
    public List<JobRow> listForProject(UUID projectId, int page, int size) {
        int bounded = Math.clamp(size, 1, 100);
        return jobs.listForProject(projectId, bounded, Math.max(page, 0) * bounded);
    }

    /** Acknowledging a cancel doesn't mean GPU work has stopped. A running job moves to CANCEL_REQUESTED. */
    @Transactional
    public JobRow cancel(CurrentUser user, UUID jobId) {
        requireOwned(user, jobId);
        JobRow job = jobs.lock(jobId).orElseThrow(() -> ApiException.notFound("Job"));
        switch (job.state()) {
            case QUEUED, RETRY_WAIT -> {
                jobs.moveTo(jobId, JobState.CANCELLED, "CANCELLED", "Cancelled before it started", null);
                jobs.addEvent(jobId, "CANCELLED", Map.of("by", "user"));
            }
            case RUNNING -> {
                jobs.moveTo(jobId, JobState.CANCEL_REQUESTED, null, null, null);
                jobs.addEvent(jobId, "CANCEL_REQUESTED", Map.of("by", "user"));
            }
            case CANCEL_REQUESTED, CANCELLED -> { }
            default -> throw ApiException.conflict("JOB_ALREADY_FINISHED", "This job has already finished");
        }
        audit.user(user, "JOB_CANCEL", "job", jobId.toString(), Map.of("from", job.state().name()));
        return jobs.find(jobId).orElseThrow();
    }

    /** A new dispatch generation, so messages from any earlier attempt are recognized as obsolete. */
    @Transactional
    public JobRow retry(CurrentUser user, UUID jobId) {
        requireOwned(user, jobId);
        JobRow job = jobs.lock(jobId).orElseThrow(() -> ApiException.notFound("Job"));
        if (!job.state().canMoveTo(JobState.QUEUED) || job.state() == JobState.RETRY_WAIT) {
            throw ApiException.conflict("JOB_NOT_RETRYABLE", "Only failed or cancelled jobs can be retried");
        }
        jobs.lockOwnerQuota(user.id());
        if (jobs.countActive(user.id()) >= properties.limits().maxActiveJobsPerUser()) {
            throw ApiException.tooMany("JOB_QUOTA_REACHED", "You already have the maximum number of active jobs");
        }
        int generation = job.dispatchGeneration() + 1;
        jobs.markRequeued(jobId, generation, true);
        UUID eventId = outbox.enqueue(jobId, generation, job.kind(), job.backendId(), job.configRevision(), job.routingKey());
        jobs.addEvent(jobId, "RETRY_REQUESTED", Map.of("event_id", eventId.toString(), "dispatch_generation", generation));
        audit.user(user, "JOB_RETRY", "job", jobId.toString(), Map.of("dispatch_generation", generation));
        return jobs.find(jobId).orElseThrow();
    }
}
