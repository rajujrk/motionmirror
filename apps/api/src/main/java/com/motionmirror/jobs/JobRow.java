package com.motionmirror.jobs;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.UUID;

public record JobRow(
        UUID id, UUID projectId, UUID ownerId, JobKind kind, JobState state, String stage, String backendId,
        String routingKey, String configRevision, String requestedSettings, String effectiveSettings, String inputs,
        int dispatchGeneration, long fencingSequence, int attemptCount, int maxAttempts, Instant nextAttemptAt,
        String failureCode, String failureDetail, Instant createdAt, Instant updatedAt, Instant finishedAt) {

    static final String COLUMNS = """
            id, project_id, owner_id, kind, state, stage, backend_id, routing_key, config_revision,
            requested_settings::text AS requested_settings, effective_settings::text AS effective_settings,
            inputs::text AS inputs, dispatch_generation, fencing_sequence, attempt_count, max_attempts,
            next_attempt_at, failure_code, failure_detail, created_at, updated_at, finished_at
            """;

    static JobRow map(ResultSet row, int index) throws SQLException {
        return new JobRow(
                row.getObject("id", UUID.class), row.getObject("project_id", UUID.class),
                row.getObject("owner_id", UUID.class), JobKind.valueOf(row.getString("kind")),
                JobState.valueOf(row.getString("state")), row.getString("stage"), row.getString("backend_id"),
                row.getString("routing_key"), row.getString("config_revision"), row.getString("requested_settings"),
                row.getString("effective_settings"), row.getString("inputs"), row.getInt("dispatch_generation"),
                row.getLong("fencing_sequence"), row.getInt("attempt_count"), row.getInt("max_attempts"),
                instant(row, "next_attempt_at"), row.getString("failure_code"), row.getString("failure_detail"),
                instant(row, "created_at"), instant(row, "updated_at"), instant(row, "finished_at"));
    }

    private static Instant instant(ResultSet row, String column) throws SQLException {
        OffsetDateTime value = row.getObject(column, OffsetDateTime.class);
        return value == null ? null : value.toInstant();
    }
}
