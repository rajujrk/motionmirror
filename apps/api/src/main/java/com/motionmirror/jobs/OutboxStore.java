package com.motionmirror.jobs;

import com.motionmirror.shared.Json;
import io.micrometer.tracing.Span;
import io.micrometer.tracing.Tracer;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class OutboxStore {

    public record OutboxEvent(UUID id, UUID jobId, int dispatchGeneration, String routingKey, String payload, int publishAttempts) {

        static OutboxEvent map(ResultSet row, int index) throws SQLException {
            return new OutboxEvent(row.getObject("id", UUID.class), row.getObject("job_id", UUID.class),
                    row.getInt("dispatch_generation"), row.getString("routing_key"), row.getString("payload"),
                    row.getInt("publish_attempts"));
        }
    }

    private static final int SCHEMA_VERSION = 1;

    private final JdbcClient jdbc;
    private final ObjectProvider<Tracer> tracer;

    public OutboxStore(JdbcClient jdbc, ObjectProvider<Tracer> tracer) {
        this.jdbc = jdbc;
        this.tracer = tracer;
    }

    /** Lets a trace follow a job across the broker. It's only ids, as the envelope contract requires. */
    private Optional<Map<String, String>> traceContext() {
        Tracer current = tracer.getIfAvailable();
        Span span = current == null ? null : current.currentSpan();
        if (span == null) {
            return Optional.empty();
        }
        String flags = Boolean.TRUE.equals(span.context().sampled()) ? "01" : "00";
        return Optional.of(Map.of("traceparent", "00-" + span.context().traceId() + "-" + span.context().spanId() + "-" + flags));
    }

    /** Called in the same transaction as the job change, so a job and its dispatch commit or fail together. */
    public UUID enqueue(UUID jobId, int generation, JobKind kind, String backendId, String configRevision, String routingKey) {
        UUID eventId = UUID.randomUUID();
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schema_version", SCHEMA_VERSION);
        envelope.put("event_id", eventId.toString());
        envelope.put("job_id", jobId.toString());
        envelope.put("dispatch_generation", generation);
        envelope.put("task_type", kind.taskType());
        envelope.put("backend_id", backendId);
        envelope.put("config_revision", configRevision);
        envelope.put("created_at", Instant.now().toString());
        traceContext().ifPresent(trace -> envelope.put("trace", trace));
        jdbc.sql("""
                INSERT INTO outbox_events (id, schema_version, job_id, dispatch_generation, routing_key, payload)
                VALUES (:id, :version, :job, :generation, :routing, CAST(:payload AS jsonb))
                """)
                .param("id", eventId).param("version", SCHEMA_VERSION).param("job", jobId)
                .param("generation", generation).param("routing", routingKey).param("payload", Json.write(envelope))
                .update();
        return eventId;
    }

    /** A short transaction that claims rows. No lock is held while talking to the broker. */
    public List<OutboxEvent> reserve(int limit, Duration reservation) {
        return jdbc.sql("""
                UPDATE outbox_events SET state = 'RESERVED', reserved_until = now() + make_interval(secs => :seconds)
                WHERE id IN (
                    SELECT id FROM outbox_events
                    WHERE (state = 'PENDING' AND next_attempt_at <= now())
                       OR (state = 'RESERVED' AND reserved_until < now())
                    ORDER BY created_at
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED)
                RETURNING id, job_id, dispatch_generation, routing_key, payload::text AS payload, publish_attempts
                """)
                .param("seconds", reservation.toMillis() / 1000.0).param("limit", limit)
                .query(OutboxEvent::map).list();
    }

    public void markPublished(UUID eventId) {
        jdbc.sql("UPDATE outbox_events SET state = 'PUBLISHED', published_at = now(), reserved_until = NULL WHERE id = :id")
                .param("id", eventId).update();
    }

    public void markFailed(UUID eventId, String error, Duration delay) {
        jdbc.sql("""
                UPDATE outbox_events SET state = 'PENDING', reserved_until = NULL, publish_attempts = publish_attempts + 1,
                    last_error = :error, next_attempt_at = now() + make_interval(secs => :seconds)
                WHERE id = :id
                """)
                .param("id", eventId).param("error", error).param("seconds", delay.toMillis() / 1000.0).update();
    }

    public long countUnpublished() {
        return jdbc.sql("SELECT count(*) FROM outbox_events WHERE state <> 'PUBLISHED'").query(Long.class).single();
    }

    public double oldestUnpublishedSeconds() {
        return jdbc.sql("SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(created_at)), 0) FROM outbox_events WHERE state <> 'PUBLISHED'")
                .query(Double.class).single();
    }
}
