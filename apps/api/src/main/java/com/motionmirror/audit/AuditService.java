package com.motionmirror.audit;

import com.motionmirror.identity.CurrentUser;
import com.motionmirror.shared.Json;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

/** Redacted audit trail. Callers pass bounded identifiers only: never prompts, tokens or signed URLs. */
@Service
public class AuditService {

    private final JdbcClient jdbc;

    public AuditService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void user(CurrentUser user, String action, String targetType, String targetId, Map<String, ?> metadata) {
        record(user.id(), user.isAdmin() ? "ADMIN" : "USER", action, targetType, targetId, metadata);
    }

    public void system(String action, String targetType, String targetId, Map<String, ?> metadata) {
        record(null, "SYSTEM", action, targetType, targetId, metadata);
    }

    public void worker(String action, String targetType, String targetId, Map<String, ?> metadata) {
        record(null, "WORKER", action, targetType, targetId, metadata);
    }

    private void record(UUID actor, String actorType, String action, String targetType, String targetId,
            Map<String, ?> metadata) {
        jdbc.sql("""
                INSERT INTO audit_events (actor_id, actor_type, action, target_type, target_id, metadata)
                VALUES (:actor, :actorType, :action, :targetType, :targetId, CAST(:metadata AS jsonb))
                """)
                .param("actor", actor)
                .param("actorType", actorType)
                .param("action", action)
                .param("targetType", targetType)
                .param("targetId", targetId)
                .param("metadata", Json.write(metadata))
                .update();
    }
}
