package com.motionmirror.retention;

import com.motionmirror.assets.ObjectStorage;
import com.motionmirror.audit.AuditService;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.shared.Json;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;

/**
 * Run after restoring a database backup. Backups hold data that has since been deleted, so every deletion recorded in
 * object storage is applied again before anyone is let back in. Applying one twice changes nothing.
 */
@Service
public class RestoreReconciler {

    public record Result(int recorded, int reapplied) {}

    static final String PREFIX = "retention/deletions/";

    private final ObjectStorage storage;
    private final DeletionService deletion;
    private final AuditService audit;

    public RestoreReconciler(ObjectStorage storage, DeletionService deletion, AuditService audit) {
        this.storage = storage;
        this.deletion = deletion;
        this.audit = audit;
    }

    public Result reconcile(CurrentUser admin) {
        List<String> keys = storage.listKeys(PREFIX);
        int reapplied = 0;
        for (String key : keys) {
            JsonNode record = Json.read(storage.readText(key));
            if (deletion.reapply(record.path("kind").asString(), UUID.fromString(record.path("target_id").asString()),
                    UUID.fromString(record.path("owner_id").asString()), UUID.fromString(record.path("id").asString()))) {
                reapplied++;
            }
        }
        audit.user(admin, "RESTORE_RECONCILED", "retention", "deletions", Map.of("recorded", keys.size(), "reapplied", reapplied));
        return new Result(keys.size(), reapplied);
    }
}
