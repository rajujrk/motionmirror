package com.motionmirror.workers;

import com.motionmirror.assets.ObjectStorage;
import com.motionmirror.config.AppProperties;
import com.motionmirror.jobs.JobRow;
import com.motionmirror.jobs.JobState;
import com.motionmirror.jobs.JobStore;
import com.motionmirror.shared.ApiException;
import com.motionmirror.shared.Json;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Fenced execution leases. Acquire locks the job row, so concurrent deliveries get exactly one winner. Every
 * later call must present the current token: a late worker can never overwrite a newer attempt.
 */
@Service
public class LeaseService {

    public static final String DUPLICATE = "DUPLICATE";
    public static final String OBSOLETE = "OBSOLETE";
    public static final String CANCELLED = "CANCELLED";

    /** Failures that retrying can't fix. Everything else is retried while attempts remain. */
    private static final Set<String> NEVER_RETRY = Set.of("INVALID_MEDIA", "UNSUPPORTED_CAPABILITY", "MODEL_MISSING",
            "CUDA_OOM", "TARGET_LOST", "INVALID_REQUEST");

    public sealed interface AcquireResult permits Acquired, Refused {}

    public record Acquired(WorkerDtos.AcquireResponse response) implements AcquireResult {}

    public record Refused(String disposition, String detail) implements AcquireResult {}

    public record AttemptRow(UUID id, UUID jobId, int attemptNo, long fencingToken, String state, Instant leaseExpiresAt, String workerId) {}

    private static final String PROBE_TYPE = "text/plain";

    private final JdbcClient jdbc;
    private final JobStore jobs;
    private final ObjectStorage storage;
    private final JobCompletionHandlers handlers;
    private final AppProperties properties;

    public LeaseService(JdbcClient jdbc, JobStore jobs, ObjectStorage storage, JobCompletionHandlers handlers,
            AppProperties properties) {
        this.jdbc = jdbc;
        this.jobs = jobs;
        this.storage = storage;
        this.handlers = handlers;
        this.properties = properties;
    }

    @Transactional
    public AcquireResult acquire(UUID jobId, UUID eventId, int generation, String workerId, Set<String> clientBackends) {
        Optional<JobRow> found = jobs.lock(jobId);
        if (found.isEmpty()) {
            return new Refused(OBSOLETE, "unknown job");
        }
        JobRow job = found.get();
        if (!clientBackends.contains(job.backendId())) {
            // A credential problem, not a stale message: the worker must not ack this as handled.
            throw new ApiException(HttpStatus.FORBIDDEN, "BACKEND_NOT_PERMITTED", "This worker can't run backend " + job.backendId());
        }
        if (job.state() == JobState.CANCELLED || job.state() == JobState.CANCEL_REQUESTED) {
            return refuse(job, eventId, workerId, CANCELLED, "job was cancelled");
        }
        if (job.state().isTerminal()) {
            return refuse(job, eventId, workerId, DUPLICATE, "job already finished");
        }
        if (generation != job.dispatchGeneration()) {
            return refuse(job, eventId, workerId, OBSOLETE, "dispatch generation " + generation + " is not current");
        }
        if (job.state() == JobState.RETRY_WAIT) {
            return refuse(job, eventId, workerId, OBSOLETE, "job is waiting for its retry");
        }
        if (job.state() == JobState.RUNNING) {
            Optional<AttemptRow> active = activeAttempt(jobId);
            if (active.isPresent() && active.get().leaseExpiresAt().isAfter(Instant.now())) {
                return refuse(job, eventId, workerId, DUPLICATE, "an attempt is already running");
            }
            active.ifPresent(attempt -> finishAttempt(attempt.id(), "EXPIRED", "LEASE_EXPIRED", "Replaced after its lease expired"));
        }
        if (job.attemptCount() >= job.maxAttempts()) {
            jobs.moveTo(jobId, JobState.FAILED, "ATTEMPTS_EXHAUSTED", "No attempts remain", null);
            jobs.addEvent(jobId, "FAILED", Map.of("code", "ATTEMPTS_EXHAUSTED"));
            return refuse(job, eventId, workerId, OBSOLETE, "no attempts remain");
        }

        long fencing = job.fencingSequence() + 1;
        int attemptNo = job.attemptCount() + 1;
        UUID attemptId = UUID.randomUUID();
        Instant expires = Instant.now().plus(properties.jobs().leaseTtl());
        jdbc.sql("""
                INSERT INTO job_attempts (id, job_id, attempt_no, worker_id, fencing_token, dispatch_generation, event_id, state, lease_expires_at)
                VALUES (:id, :job, :no, :worker, :fencing, :generation, :event, 'ACTIVE', :expires)
                """)
                .param("id", attemptId).param("job", jobId).param("no", attemptNo).param("worker", workerId)
                .param("fencing", fencing).param("generation", generation).param("event", eventId)
                .param("expires", time(expires)).update();
        jobs.startAttempt(jobId, fencing, attemptNo);
        recordDelivery(jobId, eventId, workerId, "ACQUIRED");
        jobs.addEvent(jobId, "ATTEMPT_STARTED", Map.of("attempt_no", attemptNo, "worker_id", workerId));
        return new Acquired(new WorkerDtos.AcquireResponse(attemptId, attemptNo, fencing, expires, requestFor(job)));
    }

    private Refused refuse(JobRow job, UUID eventId, String workerId, String disposition, String detail) {
        recordDelivery(job.id(), eventId, workerId, disposition);
        return new Refused(disposition, detail);
    }

    private Map<String, Object> requestFor(JobRow job) {
        Map<String, Object> request = new LinkedHashMap<>();
        request.put("job_id", job.id().toString());
        request.put("kind", job.kind().name());
        request.put("backend_id", job.backendId());
        request.put("config_revision", job.configRevision());
        request.put("settings", Json.read(job.effectiveSettings()));
        request.put("inputs", Json.read(job.inputs()));
        return request;
    }

    private void recordDelivery(UUID jobId, UUID eventId, String workerId, String disposition) {
        jdbc.sql("""
                INSERT INTO worker_deliveries (event_id, job_id, worker_id, disposition)
                VALUES (:event, :job, :worker, :disposition)
                ON CONFLICT (event_id, worker_id) DO UPDATE SET deliveries = worker_deliveries.deliveries + 1,
                    last_seen = now(), disposition = EXCLUDED.disposition
                """)
                .param("event", eventId).param("job", jobId).param("worker", workerId).param("disposition", disposition).update();
    }

    @Transactional
    public WorkerDtos.HeartbeatResponse heartbeat(UUID jobId, UUID attemptId, long token) {
        AttemptRow attempt = requireCurrent(jobId, attemptId, token);
        Instant expires = Instant.now().plus(properties.jobs().leaseTtl());
        jdbc.sql("UPDATE job_attempts SET lease_expires_at = :expires, last_heartbeat_at = now() WHERE id = :id")
                .param("expires", time(expires)).param("id", attempt.id()).update();
        JobRow job = jobs.find(jobId).orElseThrow();
        return new WorkerDtos.HeartbeatResponse(expires, job.state() == JobState.CANCEL_REQUESTED);
    }

    @Transactional
    public void progress(UUID jobId, WorkerDtos.ProgressRequest request) {
        AttemptRow attempt = requireCurrent(jobId, request.attemptId(), request.fencingToken());
        JobRow job = jobs.find(jobId).orElseThrow();
        Map<String, Object> progress = new LinkedHashMap<>();
        progress.put("stage", request.stage());
        progress.put("current", request.current());
        progress.put("total", request.total());
        progress.put("unit", request.unit());
        jdbc.sql("UPDATE job_attempts SET stage = :stage, progress = CAST(:progress AS jsonb) WHERE id = :id")
                .param("stage", request.stage()).param("progress", Json.write(progress)).param("id", attempt.id()).update();
        if (!request.stage().equals(job.stage())) {
            jobs.updateStage(jobId, request.stage());
            jobs.addEvent(jobId, "STAGE", Map.of("stage", request.stage()));
        }
    }

    /** Output is attached only after the storage service confirms it exists at the attempt's own prefix. */
    @Transactional
    public JobState complete(UUID jobId, WorkerDtos.CompleteRequest request) {
        // A deleted job's worker is told it lost the lease, so it stops instead of retrying a 404.
        JobRow job = jobs.lock(jobId).orElseThrow(LeaseService::leaseLost);
        Optional<AttemptRow> previous = findAttempt(request.attemptId());
        if (previous.isPresent() && previous.get().state().equals("SUCCEEDED") && previous.get().jobId().equals(jobId)
                && previous.get().fencingToken() == request.fencingToken()) {
            return job.state();
        }
        AttemptRow attempt = requireCurrent(jobId, request.attemptId(), request.fencingToken());
        if (job.state() == JobState.CANCEL_REQUESTED) {
            finishAttempt(attempt.id(), "CANCELLED", "CANCELLED", "Finished after cancellation was requested");
            jobs.moveTo(jobId, JobState.CANCELLED, "CANCELLED", "Cancelled by the user", null);
            jobs.addEvent(jobId, "CANCELLED", Map.of("late_completion", true));
            handlers.onFailed(job, "CANCELLED", "Cancelled by the user");
            return JobState.CANCELLED;
        }
        String prefix = attemptPrefix(attempt.id());
        for (WorkerDtos.OutputRef output : request.outputs()) {
            if (!output.key().startsWith(prefix) || output.key().contains("..")) {
                throw ApiException.badRequest("OUTPUT_KEY_REJECTED", "Output is outside this attempt's prefix");
            }
            ObjectStorage.ObjectInfo stored = storage.head(output.key())
                    .orElseThrow(() -> ApiException.badRequest("OUTPUT_MISSING", "Output " + output.name() + " was not uploaded"));
            if (stored.sizeBytes() != output.sizeBytes()) {
                throw ApiException.badRequest("OUTPUT_SIZE_MISMATCH", "Output " + output.name() + " has the wrong size");
            }
            jobs.insertOutput(jobId, attempt.id(), output.name(), output.key(), output.sha256(), output.sizeBytes(),
                    output.contentType(), Json.write(output.media() == null ? Map.of() : output.media()));
        }
        if (request.effectiveSettings() != null) {
            jobs.mergeEffectiveSettings(jobId, Json.write(request.effectiveSettings()));
        }
        finishAttempt(attempt.id(), "SUCCEEDED", null, null);
        jobs.moveTo(jobId, JobState.SUCCEEDED, null, null, null);
        jobs.addEvent(jobId, "SUCCEEDED", Map.of("attempt_no", attempt.attemptNo(), "outputs", request.outputs().size()));
        handlers.onSucceeded(job, request);
        return JobState.SUCCEEDED;
    }

    /** Spring Boot owns retry policy. The worker reports what happened; this decides what happens next. */
    @Transactional
    public JobState fail(UUID jobId, WorkerDtos.FailRequest request) {
        JobRow job = jobs.lock(jobId).orElseThrow(LeaseService::leaseLost);
        AttemptRow attempt = requireCurrent(jobId, request.attemptId(), request.fencingToken());
        finishAttempt(attempt.id(), "FAILED", request.code(), request.detail());
        return applyFailure(job, request.code(), request.detail(), request.retryable());
    }

    JobState applyFailure(JobRow job, String code, String detail, boolean retryable) {
        UUID jobId = job.id();
        if (job.state() == JobState.CANCEL_REQUESTED || "CANCELLED".equals(code)) {
            jobs.moveTo(jobId, JobState.CANCELLED, "CANCELLED", "Cancelled by the user", null);
            jobs.addEvent(jobId, "CANCELLED", Map.of());
            handlers.onFailed(job, "CANCELLED", "Cancelled by the user");
            return JobState.CANCELLED;
        }
        boolean attemptsRemain = currentAttemptCount(jobId) < job.maxAttempts();
        if (retryable && attemptsRemain && !NEVER_RETRY.contains(code)) {
            long delayMillis = properties.jobs().retryBaseDelay().toMillis() << Math.min(currentAttemptCount(jobId) - 1, 6);
            Instant next = Instant.now().plusMillis(delayMillis);
            jobs.moveTo(jobId, JobState.RETRY_WAIT, code, detail, next);
            jobs.addEvent(jobId, "RETRY_SCHEDULED", Map.of("code", code, "next_attempt_at", next.toString()));
            return JobState.RETRY_WAIT;
        }
        jobs.moveTo(jobId, JobState.FAILED, code, detail, null);
        jobs.addEvent(jobId, "FAILED", Map.of("code", code));
        handlers.onFailed(job, code, detail);
        return JobState.FAILED;
    }

    private int currentAttemptCount(UUID jobId) {
        return jobs.find(jobId).orElseThrow().attemptCount();
    }

    /** Signed URLs are issued only under a current lease, and only for this job's inputs and this attempt's prefix. */
    @Transactional(readOnly = true)
    public WorkerDtos.ArtifactAccessResponse artifactAccess(UUID jobId, WorkerDtos.ArtifactAccessRequest request) {
        AttemptRow attempt = requireCurrent(jobId, request.attemptId(), request.fencingToken());
        JobRow job = jobs.find(jobId).orElseThrow();
        Map<String, String> reads = new LinkedHashMap<>();
        for (String name : request.reads() == null ? List.<String>of() : request.reads()) {
            reads.put(name, storage.presignWorkerRead(handlers.resolveReadKey(job, name)));
        }
        Map<String, WorkerDtos.ArtifactWrite> writes = new LinkedHashMap<>();
        for (String name : request.writes() == null ? List.<String>of() : request.writes()) {
            String key = attemptPrefix(attempt.id()) + name;
            String type = contentType(name);
            writes.put(name, new WorkerDtos.ArtifactWrite(key, storage.presignWorkerWrite(key, type), type));
        }
        return new WorkerDtos.ArtifactAccessResponse(reads, writes);
    }

    /** Only the adapter this job's own snapshot names, and only under a current lease. */
    @Transactional(readOnly = true)
    public WorkerDtos.AdapterAccessResponse adapterAccess(UUID adapterId, WorkerDtos.AdapterAccessRequest request) {
        AttemptRow attempt = findAttempt(request.attemptId()).orElseThrow(LeaseService::leaseLost);
        requireCurrent(attempt.jobId(), request.attemptId(), request.fencingToken());
        JobRow job = jobs.find(attempt.jobId()).orElseThrow();
        String named = Json.read(job.inputs()).path("adapter").path("id").asString("");
        if (!adapterId.toString().equals(named)) {
            throw new ApiException(HttpStatus.FORBIDDEN, "ADAPTER_NOT_IN_JOB", "This job doesn't use that adapter");
        }
        var row = jdbc.sql("SELECT artifact_key, sha256, scale FROM model_adapters WHERE id = :id").param("id", adapterId).query().singleRow();
        return new WorkerDtos.AdapterAccessResponse(storage.presignWorkerRead((String) row.get("artifact_key")),
                (String) row.get("sha256"), ((Number) row.get("scale")).doubleValue());
    }

    /** The purger removes it, so a probe can't be left behind even if the worker stops halfway through. */
    @Transactional
    public WorkerDtos.StorageProbe storageProbe() {
        String key = "probes/" + UUID.randomUUID();
        jdbc.sql("INSERT INTO deletion_tombstones (object_key, owner_id) VALUES (:key, :system)")
                .param("key", key).param("system", new UUID(0, 0)).update();
        return new WorkerDtos.StorageProbe(storage.presignWorkerWrite(key, PROBE_TYPE), storage.presignWorkerRead(key), PROBE_TYPE);
    }

    public static String attemptPrefix(UUID attemptId) {
        return "attempts/" + attemptId + "/";
    }

    static String contentType(String name) {
        if (name.endsWith(".mp4")) return "video/mp4";
        if (name.endsWith(".png")) return "image/png";
        if (name.endsWith(".jpg")) return "image/jpeg";
        if (name.endsWith(".json")) return "application/json";
        return "application/octet-stream";
    }

    private AttemptRow requireCurrent(UUID jobId, UUID attemptId, long token) {
        AttemptRow attempt = findAttempt(attemptId).filter(row -> row.jobId().equals(jobId)).orElseThrow(LeaseService::leaseLost);
        JobRow job = jobs.find(jobId).orElseThrow(LeaseService::leaseLost);
        boolean current = attempt.state().equals("ACTIVE")
                && attempt.fencingToken() == token
                && job.fencingSequence() == token
                && attempt.leaseExpiresAt().isAfter(Instant.now());
        if (!current) {
            throw leaseLost();
        }
        return attempt;
    }

    private static ApiException leaseLost() {
        return new ApiException(HttpStatus.CONFLICT, "LEASE_LOST", "This attempt no longer holds the lease");
    }

    private Optional<AttemptRow> activeAttempt(UUID jobId) {
        return jdbc.sql("SELECT id, job_id, attempt_no, fencing_token, state, lease_expires_at, worker_id FROM job_attempts WHERE job_id = :job AND state = 'ACTIVE'")
                .param("job", jobId).query(LeaseService::mapAttempt).optional();
    }

    private Optional<AttemptRow> findAttempt(UUID attemptId) {
        return jdbc.sql("SELECT id, job_id, attempt_no, fencing_token, state, lease_expires_at, worker_id FROM job_attempts WHERE id = :id")
                .param("id", attemptId).query(LeaseService::mapAttempt).optional();
    }

    private static AttemptRow mapAttempt(java.sql.ResultSet row, int index) throws java.sql.SQLException {
        return new AttemptRow(row.getObject("id", UUID.class), row.getObject("job_id", UUID.class), row.getInt("attempt_no"),
                row.getLong("fencing_token"), row.getString("state"),
                row.getObject("lease_expires_at", OffsetDateTime.class).toInstant(), row.getString("worker_id"));
    }

    void finishAttempt(UUID attemptId, String state, String code, String detail) {
        jdbc.sql("UPDATE job_attempts SET state = :state, finished_at = now(), failure_code = :code, failure_detail = :detail WHERE id = :id")
                .param("state", state).param("code", code).param("detail", detail).param("id", attemptId).update();
    }

    private static OffsetDateTime time(Instant value) {
        return value.atOffset(java.time.ZoneOffset.UTC);
    }
}
