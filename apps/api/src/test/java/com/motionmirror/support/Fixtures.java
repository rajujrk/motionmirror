package com.motionmirror.support;

import com.motionmirror.assets.AssetEntity;
import com.motionmirror.jobs.JobKind;
import com.motionmirror.jobs.JobRow;
import com.motionmirror.jobs.JobService;
import java.util.Map;
import java.util.UUID;
import com.motionmirror.shared.Json;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** Direct database setup for tests that aren't about the setup path itself. */
@Component
public class Fixtures {

    private final JdbcClient jdbc;
    private final JobService jobs;

    public Fixtures(JdbcClient jdbc, JobService jobs) {
        this.jdbc = jdbc;
        this.jobs = jobs;
    }

    public UUID user() {
        UUID id = UUID.randomUUID();
        jdbc.sql("INSERT INTO users (id, issuer, subject) VALUES (:id, 'https://issuer.test', :subject)")
                .param("id", id).param("subject", "sub-" + id).update();
        return id;
    }

    public UUID project(UUID owner) {
        UUID id = UUID.randomUUID();
        jdbc.sql("INSERT INTO projects (id, owner_id, name) VALUES (:id, :owner, 'Test project')")
                .param("id", id).param("owner", owner).update();
        return id;
    }

    public UUID readyAsset(UUID owner, UUID project, String role) {
        UUID id = UUID.randomUUID();
        String media = "{\"duration_seconds\": 10, \"display_width\": 720, \"display_height\": 1280}";
        jdbc.sql("""
                INSERT INTO assets (id, project_id, owner_id, role, status, display_name, declared_type, declared_bytes,
                    upload_key, object_key, size_bytes, checksum_sha256, detected_type, media, upload_expires_at, consent_confirmed)
                VALUES (:id, :project, :owner, :role, 'READY', 'file', :type, 10, :upload, :object, 10, :sha, :type, CAST(:media AS jsonb), now(), true)
                """)
                .param("id", id).param("project", project).param("owner", owner).param("role", role)
                .param("type", AssetEntity.DRIVING_VIDEO.equals(role) ? "video/mp4" : "image/png")
                .param("upload", "uploads/" + id).param("object", "assets/" + id).param("sha", "a".repeat(64)).param("media", media)
                .update();
        return id;
    }

    /** Submits a job the normal way, so the outbox row exists too. */
    public JobRow queuedJob(UUID owner, UUID project, JobKind kind) {
        UUID asset = readyAsset(owner, project, AssetEntity.DRIVING_VIDEO);
        return jobs.submit(owner, new JobService.Submission(project, kind, "preview@1", "{}", "{\"seed\":1}",
                Json.write(Map.of("asset_id", asset.toString(), "source_asset_id", asset.toString())), null, true));
    }
}
