package com.motionmirror.retention;

import com.motionmirror.assets.ObjectStorage;
import com.motionmirror.audit.AuditService;
import com.motionmirror.config.AppProperties;
import com.motionmirror.shared.Json;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Removes the bytes behind tombstones, abandoned uploads and projects past their retention. A worker can still hold a
 * signed upload URL when a project is deleted, so a tombstone stays open until those URLs can no longer be used.
 */
@Component
@ConditionalOnProperty(name = "motionmirror.jobs.reconciler-enabled", havingValue = "true", matchIfMissing = true)
public class StoragePurger {

    private static final Logger log = LoggerFactory.getLogger(StoragePurger.class);
    private static final int BATCH = 100;

    private final JdbcClient jdbc;
    private final ObjectStorage storage;
    private final AuditService audit;
    private final DeletionService deletion;
    private final Duration urlLifetime;
    private final int projectDays;

    public StoragePurger(JdbcClient jdbc, ObjectStorage storage, AuditService audit, DeletionService deletion, AppProperties properties) {
        this.jdbc = jdbc;
        this.storage = storage;
        this.audit = audit;
        this.deletion = deletion;
        this.urlLifetime = properties.storage().workerUrlTtl().plusMinutes(1);
        this.projectDays = properties.retention().projectDays();
    }

    @Scheduled(fixedDelayString = "${motionmirror.retention.purge-interval:60s}")
    public void run() {
        try {
            if (projectDays > 0) {
                expireIdleProjects(projectDays);
            }
            exportDeletionLog();
            purgeTombstones(urlLifetime);
            expireAbandonedUploads();
        } catch (RuntimeException error) {
            log.warn("storage purge pass failed: {}", error.getClass().getSimpleName());
        }
    }

    /** Pages by key and survives a failing key, so one bad object can't hold back every deletion behind it. */
    public void purgeTombstones(Duration closeAfter) {
        String after = "";
        while (true) {
            List<String> keys = jdbc.sql("""
                    SELECT object_key FROM deletion_tombstones
                    WHERE purged_at IS NULL AND object_key > :after ORDER BY object_key LIMIT :limit
                    """).param("after", after).param("limit", BATCH).query(String.class).list();
            for (String key : keys) {
                purgeOne(key, closeAfter);
            }
            if (keys.size() < BATCH) {
                return;
            }
            after = keys.getLast();
        }
    }

    private void purgeOne(String key, Duration closeAfter) {
        try {
            if (key.endsWith("/")) {
                storage.deletePrefix(key);
            } else {
                storage.delete(key);
            }
        } catch (RuntimeException error) {
            log.warn("could not purge an object yet, will retry: {}", error.getClass().getSimpleName());
            return;
        }
        jdbc.sql("""
                UPDATE deletion_tombstones SET purged_at = now()
                WHERE object_key = :key AND deleted_at <= now() - make_interval(secs => :seconds)
                """).param("key", key).param("seconds", closeAfter.toSeconds()).update();
    }

    /** Copied out of the database, because restoring a backup would roll back the only record of these deletions. */
    public void exportDeletionLog() {
        List<Map<String, Object>> pending = jdbc.sql("""
                SELECT id, kind, target_id, owner_id FROM deletion_log WHERE exported_at IS NULL ORDER BY deleted_at LIMIT :limit
                """).param("limit", BATCH).query().listOfRows();
        for (Map<String, Object> row : pending) {
            String record = Json.write(Map.of("id", row.get("id").toString(), "kind", row.get("kind"),
                    "target_id", row.get("target_id").toString(), "owner_id", row.get("owner_id").toString()));
            storage.writeText(RestoreReconciler.PREFIX + row.get("id") + ".json", record);
            jdbc.sql("UPDATE deletion_log SET exported_at = now() WHERE id = :id").param("id", row.get("id")).update();
        }
    }

    /** Uploads that never finished are removed once their form expires. */
    public void expireAbandonedUploads() {
        List<Map<String, Object>> abandoned = jdbc.sql("""
                SELECT id, upload_key FROM assets
                WHERE status = 'PENDING_UPLOAD' AND upload_expires_at < now() - interval '1 hour' LIMIT :limit
                """).param("limit", BATCH).query().listOfRows();
        for (Map<String, Object> row : abandoned) {
            storage.delete((String) row.get("upload_key"));
            jdbc.sql("UPDATE assets SET status = 'DELETED', updated_at = now() WHERE id = :id").param("id", row.get("id")).update();
            audit.system("UPLOAD_ABANDONED", "asset", row.get("id").toString(), Map.of());
        }
    }

    /** Activity counts as use, and a project with work still running is never taken away from under it. */
    public void expireIdleProjects(int days) {
        List<UUID> idle = jdbc.sql("""
                SELECT p.id FROM projects p
                WHERE p.status = 'ACTIVE' AND p.updated_at < now() - make_interval(days => :days)
                  AND NOT EXISTS (SELECT 1 FROM generation_jobs j WHERE j.project_id = p.id
                      AND (j.created_at >= now() - make_interval(days => :days)
                           OR j.state NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED')))
                  AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.project_id = p.id
                      AND a.created_at >= now() - make_interval(days => :days))
                LIMIT :limit
                """).param("days", days).param("limit", BATCH).query(UUID.class).list();
        idle.forEach(deletion::expireProject);
    }
}
