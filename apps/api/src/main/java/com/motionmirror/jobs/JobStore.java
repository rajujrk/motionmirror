package com.motionmirror.jobs;

import com.motionmirror.shared.Json;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** Explicit SQL: job concurrency depends on exact locking and conditional updates. */
@Repository
public class JobStore {

    private final JdbcClient jdbc;

    public JobStore(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public record NewJob(UUID id, UUID projectId, UUID ownerId, JobKind kind, String backendId, String routingKey,
            String configRevision, String requestedSettings, String effectiveSettings, String inputs,
            String idempotencyKey, int maxAttempts, boolean consentConfirmed) {}

    public void insert(NewJob job) {
        jdbc.sql("""
                INSERT INTO generation_jobs (id, project_id, owner_id, kind, state, backend_id, routing_key, config_revision,
                    requested_settings, effective_settings, inputs, idempotency_key, max_attempts, consent_confirmed)
                VALUES (:id, :projectId, :ownerId, :kind, 'QUEUED', :backendId, :routingKey, :configRevision,
                    CAST(:requested AS jsonb), CAST(:effective AS jsonb), CAST(:inputs AS jsonb), :idempotencyKey, :maxAttempts, :consent)
                """)
                .param("id", job.id()).param("projectId", job.projectId()).param("ownerId", job.ownerId())
                .param("kind", job.kind().name()).param("backendId", job.backendId())
                .param("routingKey", job.routingKey()).param("configRevision", job.configRevision())
                .param("requested", job.requestedSettings()).param("effective", job.effectiveSettings())
                .param("inputs", job.inputs()).param("idempotencyKey", job.idempotencyKey())
                .param("maxAttempts", job.maxAttempts()).param("consent", job.consentConfirmed())
                .update();
    }

    public Optional<JobRow> find(UUID id) {
        return jdbc.sql("SELECT " + JobRow.COLUMNS + " FROM generation_jobs WHERE id = :id")
                .param("id", id).query(JobRow::map).optional();
    }

    public Optional<JobRow> findOwned(UUID id, UUID ownerId) {
        return jdbc.sql("SELECT " + JobRow.COLUMNS + " FROM generation_jobs WHERE id = :id AND owner_id = :owner")
                .param("id", id).param("owner", ownerId).query(JobRow::map).optional();
    }

    public Optional<JobRow> findByIdempotencyKey(UUID ownerId, String key) {
        return jdbc.sql("SELECT " + JobRow.COLUMNS + " FROM generation_jobs WHERE owner_id = :owner AND idempotency_key = :key")
                .param("owner", ownerId).param("key", key).query(JobRow::map).optional();
    }

    public Optional<JobRow> lock(UUID id) {
        return jdbc.sql("SELECT " + JobRow.COLUMNS + " FROM generation_jobs WHERE id = :id FOR UPDATE")
                .param("id", id).query(JobRow::map).optional();
    }

    public List<JobRow> listForProject(UUID projectId, int limit, int offset) {
        return jdbc.sql("SELECT " + JobRow.COLUMNS + " FROM generation_jobs WHERE project_id = :project ORDER BY created_at DESC LIMIT :limit OFFSET :offset")
                .param("project", projectId).param("limit", limit).param("offset", offset).query(JobRow::map).list();
    }

    public int countActive(UUID ownerId) {
        return jdbc.sql("SELECT count(*) FROM generation_jobs WHERE owner_id = :owner AND kind <> 'VALIDATE' AND state IN ('QUEUED','RUNNING','RETRY_WAIT','CANCEL_REQUESTED')")
                .param("owner", ownerId).query(Integer.class).single();
    }

    /** Serializes one user's submissions so the hard quota can't be raced, and survives Redis restarts. */
    public void lockOwnerQuota(UUID ownerId) {
        jdbc.sql("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
                .param("key", "quota:" + ownerId).query(Object.class).optional();
    }

    public void moveTo(UUID id, JobState state, String failureCode, String failureDetail, Instant nextAttemptAt) {
        boolean terminal = state.isTerminal();
        jdbc.sql("""
                UPDATE generation_jobs SET state = :state, failure_code = :code, failure_detail = :detail,
                    next_attempt_at = :next, updated_at = now(),
                    finished_at = CASE WHEN :terminal THEN now() ELSE NULL END,
                    stage = CASE WHEN :terminal THEN stage ELSE NULL END
                WHERE id = :id
                """)
                .param("id", id).param("state", state.name()).param("code", failureCode).param("detail", failureDetail)
                .param("next", time(nextAttemptAt)).param("terminal", terminal).update();
    }

    public void markRequeued(UUID id, int newGeneration, boolean resetAttempts) {
        jdbc.sql("""
                UPDATE generation_jobs SET state = 'QUEUED', dispatch_generation = :generation, next_attempt_at = NULL,
                    failure_code = NULL, failure_detail = NULL, finished_at = NULL, stage = NULL, updated_at = now(),
                    attempt_count = CASE WHEN :reset THEN 0 ELSE attempt_count END
                WHERE id = :id
                """)
                .param("id", id).param("generation", newGeneration).param("reset", resetAttempts).update();
    }

    public void startAttempt(UUID id, long fencing, int attemptCount) {
        jdbc.sql("""
                UPDATE generation_jobs SET state = 'RUNNING', fencing_sequence = :fencing, attempt_count = :attempts,
                    next_attempt_at = NULL, failure_code = NULL, failure_detail = NULL, updated_at = now()
                WHERE id = :id
                """)
                .param("id", id).param("fencing", fencing).param("attempts", attemptCount).update();
    }

    public void updateStage(UUID id, String stage) {
        jdbc.sql("UPDATE generation_jobs SET stage = :stage, updated_at = now() WHERE id = :id")
                .param("id", id).param("stage", stage).update();
    }

    public void mergeEffectiveSettings(UUID id, String json) {
        jdbc.sql("UPDATE generation_jobs SET effective_settings = effective_settings || CAST(:json AS jsonb), updated_at = now() WHERE id = :id")
                .param("id", id).param("json", json).update();
    }

    public void addEvent(UUID jobId, String type, Map<String, ?> detail) {
        jdbc.sql("INSERT INTO job_events (job_id, type, detail) VALUES (:job, :type, CAST(:detail AS jsonb))")
                .param("job", jobId).param("type", type).param("detail", Json.write(detail)).update();
    }

    public record EventRow(long id, Instant occurredAt, String type, String detail) {}

    public List<EventRow> events(UUID jobId) {
        return jdbc.sql("SELECT id, occurred_at, type, detail::text AS detail FROM job_events WHERE job_id = :job ORDER BY id")
                .param("job", jobId)
                .query((row, index) -> new EventRow(row.getLong("id"),
                        row.getObject("occurred_at", OffsetDateTime.class).toInstant(), row.getString("type"), row.getString("detail")))
                .list();
    }

    public record OutputRow(UUID id, UUID jobId, UUID attemptId, String name, String objectKey, String sha256,
            long sizeBytes, String contentType, String media) {}

    public List<OutputRow> outputs(UUID jobId) {
        return jdbc.sql("SELECT id, job_id, attempt_id, name, object_key, sha256, size_bytes, content_type, media::text AS media FROM outputs WHERE job_id = :job ORDER BY name")
                .param("job", jobId)
                .query((row, index) -> new OutputRow(row.getObject("id", UUID.class), row.getObject("job_id", UUID.class),
                        row.getObject("attempt_id", UUID.class), row.getString("name"), row.getString("object_key"),
                        row.getString("sha256"), row.getLong("size_bytes"), row.getString("content_type"), row.getString("media")))
                .list();
    }

    public void insertOutput(UUID jobId, UUID attemptId, String name, String objectKey, String sha256, long sizeBytes,
            String contentType, String media) {
        jdbc.sql("""
                INSERT INTO outputs (id, job_id, attempt_id, name, object_key, sha256, size_bytes, content_type, media)
                VALUES (:id, :job, :attempt, :name, :key, :sha, :size, :type, CAST(:media AS jsonb))
                """)
                .param("id", UUID.randomUUID()).param("job", jobId).param("attempt", attemptId).param("name", name)
                .param("key", objectKey).param("sha", sha256).param("size", sizeBytes).param("type", contentType)
                .param("media", media).update();
    }

    static OffsetDateTime time(Instant value) {
        return value == null ? null : value.atOffset(ZoneOffset.UTC);
    }
}
