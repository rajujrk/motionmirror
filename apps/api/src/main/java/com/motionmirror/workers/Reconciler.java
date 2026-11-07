package com.motionmirror.workers;

import com.motionmirror.config.AppProperties;
import com.motionmirror.jobs.JobRow;
import com.motionmirror.jobs.JobState;
import com.motionmirror.jobs.JobStore;
import com.motionmirror.jobs.OutboxStore;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * Repairs expired leases, starts due retries and redispatches jobs that really lost their dispatch. Ordinary
 * queue wait is not loss, so a queued job is only redispatched after a long, configured silence.
 */
@Component
@ConditionalOnProperty(name = "motionmirror.jobs.reconciler-enabled", havingValue = "true", matchIfMissing = true)
public class Reconciler {

    private static final Logger log = LoggerFactory.getLogger(Reconciler.class);
    private static final String ADVISORY_LOCK = "mm-reconciler";
    private static final int BATCH = 50;

    private final JdbcClient jdbc;
    private final JobStore jobs;
    private final OutboxStore outbox;
    private final LeaseService leases;
    private final TransactionTemplate transactions;
    private final AppProperties.Jobs config;
    private final MeterRegistry metrics;

    public Reconciler(JdbcClient jdbc, JobStore jobs, OutboxStore outbox, LeaseService leases,
            TransactionTemplate transactions, AppProperties properties, MeterRegistry metrics) {
        this.jdbc = jdbc;
        this.jobs = jobs;
        this.outbox = outbox;
        this.leases = leases;
        this.transactions = transactions;
        this.config = properties.jobs();
        this.metrics = metrics;
    }

    @Scheduled(fixedDelayString = "${motionmirror.jobs.reconcile-interval}")
    public void run() {
        try {
            transactions.executeWithoutResult(status -> {
                // Only one API replica reconciles at a time.
                Boolean locked = jdbc.sql("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")
                        .param("key", ADVISORY_LOCK).query(Boolean.class).single();
                if (Boolean.TRUE.equals(locked)) {
                    reconcileOnce();
                }
            });
        } catch (RuntimeException error) {
            log.warn("reconcile pass failed: {}", error.getClass().getSimpleName());
        }
    }

    void reconcileOnce() {
        expireLeases();
        startDueRetries();
        redispatchLost();
    }

    private void expireLeases() {
        List<Map<String, Object>> expired = jdbc.sql("""
                SELECT id, job_id FROM job_attempts WHERE state = 'ACTIVE' AND lease_expires_at < now() LIMIT :limit
                """).param("limit", BATCH).query().listOfRows();
        for (Map<String, Object> row : expired) {
            UUID jobId = (UUID) row.get("job_id");
            JobRow job = jobs.lock(jobId).orElseThrow();
            leases.finishAttempt((UUID) row.get("id"), "EXPIRED", "LEASE_EXPIRED", "Worker stopped sending heartbeats");
            jobs.addEvent(jobId, "LEASE_EXPIRED", Map.of());
            metrics.counter("mm.lease.expired").increment();
            if (!job.state().isTerminal()) {
                leases.applyFailure(job, "LEASE_EXPIRED", "The worker stopped responding", true);
            }
        }
    }

    private void startDueRetries() {
        List<UUID> due = jdbc.sql("""
                SELECT id FROM generation_jobs WHERE state = 'RETRY_WAIT' AND next_attempt_at <= now()
                ORDER BY next_attempt_at LIMIT :limit
                """).param("limit", BATCH).query(UUID.class).list();
        due.forEach(this::redispatch);
    }

    private void redispatchLost() {
        List<UUID> lost = jdbc.sql("""
                SELECT j.id FROM generation_jobs j
                WHERE j.state = 'QUEUED'
                  AND j.updated_at < now() - make_interval(secs => :seconds)
                  AND NOT EXISTS (SELECT 1 FROM outbox_events o WHERE o.job_id = j.id AND o.state <> 'PUBLISHED')
                ORDER BY j.updated_at LIMIT :limit
                """)
                .param("seconds", config.queuedRedispatchAfter().toSeconds()).param("limit", BATCH).query(UUID.class).list();
        lost.forEach(this::redispatch);
    }

    /** A new generation makes any older message obsolete, so a late delivery can't start a second run. */
    private void redispatch(UUID jobId) {
        JobRow job = jobs.lock(jobId).orElseThrow();
        if (job.state() != JobState.RETRY_WAIT && job.state() != JobState.QUEUED) {
            return;
        }
        int generation = job.dispatchGeneration() + 1;
        jobs.markRequeued(jobId, generation, false);
        UUID eventId = outbox.enqueue(jobId, generation, job.kind(), job.backendId(), job.configRevision(), job.routingKey());
        jobs.addEvent(jobId, "REDISPATCHED", Map.of("event_id", eventId.toString(), "dispatch_generation", generation));
        metrics.counter("mm.jobs.redispatched").increment();
    }
}
