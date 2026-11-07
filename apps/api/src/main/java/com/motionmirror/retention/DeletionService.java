package com.motionmirror.retention;

import com.motionmirror.assets.AssetEntity;
import com.motionmirror.assets.AssetRepository;
import com.motionmirror.audit.AuditService;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.jobs.JobKind;
import com.motionmirror.projects.ProjectEntity;
import com.motionmirror.projects.ProjectRepository;
import com.motionmirror.projects.ProjectService;
import com.motionmirror.shared.ApiException;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Deletion revokes access and stops work in one transaction, then records every storage key as a tombstone for the
 * purger. Anything derived from the deleted data goes with it, because someone removing their photo also expects the
 * videos made from it to go. Job rows are removed rather than cancelled: they hold prompts and marks, and a worker
 * still running one gets LEASE_LOST at its next call.
 */
@Service
public class DeletionService {

    private final JdbcClient jdbc;
    private final ProjectService projects;
    private final ProjectRepository projectRepository;
    private final AssetRepository assets;
    private final AuditService audit;

    public DeletionService(JdbcClient jdbc, ProjectService projects, ProjectRepository projectRepository,
            AssetRepository assets, AuditService audit) {
        this.jdbc = jdbc;
        this.projects = projects;
        this.projectRepository = projectRepository;
        this.assets = assets;
        this.audit = audit;
    }

    @Transactional
    public void deleteProject(CurrentUser user, UUID projectId) {
        ProjectEntity project = projects.requireOwned(user, projectId);
        purgeProject(project);
        record("PROJECT", projectId, project.getOwnerId());
        audit.user(user, "PROJECT_DELETED", "project", projectId.toString(), Map.of());
    }

    /** The retention policy's path. It audits as the system, because no person asked for this one. */
    @Transactional
    public void expireProject(UUID projectId) {
        projectRepository.findById(projectId).filter(project -> "ACTIVE".equals(project.getStatus())).ifPresent(project -> {
            purgeProject(project);
            record("PROJECT", projectId, project.getOwnerId());
            audit.system("PROJECT_EXPIRED", "project", projectId.toString(), Map.of());
        });
    }

    private void purgeProject(ProjectEntity project) {
        UUID projectId = project.getId();
        removeJobs(jdbc.sql("SELECT id FROM generation_jobs WHERE project_id = :project")
                .param("project", projectId).query(UUID.class).list(), project.getOwnerId());
        jdbc.sql("DELETE FROM preprocessing_artifacts WHERE project_id = :project").param("project", projectId).update();
        jdbc.sql("DELETE FROM subject_selections WHERE project_id = :project").param("project", projectId).update();
        jdbc.sql("DELETE FROM appearance_profiles WHERE project_id = :project").param("project", projectId).update();
        for (AssetEntity asset : assets.findByProjectId(projectId)) {
            tombstoneAsset(asset);
            asset.markDeleted();
            assets.save(asset);
        }
        project.markDeleted();
        projectRepository.save(project);
    }

    /** Removes the file and whatever was made from it. A photo only listed as an extra view was never used, so results stay. */
    @Transactional
    public void deleteAsset(CurrentUser user, UUID assetId) {
        AssetEntity asset = assets.findByIdAndOwnerId(assetId, user.id())
                .filter(found -> !AssetEntity.DELETED.equals(found.getStatus()))
                .orElseThrow(() -> ApiException.notFound("Asset"));
        String role = asset.getRole();
        purgeAsset(asset);
        record("ASSET", assetId, user.id());
        audit.user(user, "ASSET_DELETED", "asset", assetId.toString(), Map.of("role", role));
    }

    private void purgeAsset(AssetEntity asset) {
        UUID assetId = asset.getId();
        removeJobs(jdbc.sql("""
                SELECT id FROM generation_jobs
                WHERE project_id = :project AND (inputs->>'asset_id' = :asset OR inputs->>'source_asset_id' = :asset
                    OR inputs->>'primary_reference_asset_id' = :asset)
                """).param("project", asset.getProjectId()).param("asset", assetId.toString()).query(UUID.class).list(), asset.getOwnerId());
        jdbc.sql("DELETE FROM preprocessing_artifacts WHERE source_asset_id = :asset").param("asset", assetId).update();
        jdbc.sql("DELETE FROM subject_selections WHERE source_asset_id = :asset").param("asset", assetId).update();
        // A profile can't exist without its primary photo. Without this one it simply loses an extra view.
        jdbc.sql("""
                DELETE FROM appearance_profiles WHERE id IN (
                    SELECT profile_id FROM reference_images WHERE asset_id = :asset AND is_primary)
                """).param("asset", assetId).update();
        jdbc.sql("DELETE FROM reference_images WHERE asset_id = :asset").param("asset", assetId).update();
        tombstoneAsset(asset);
        asset.markDeleted();
        assets.save(asset);
    }

    /** One result. Masks are shared by later jobs, so they only go with their clip. */
    @Transactional
    public void deleteResult(CurrentUser user, UUID jobId) {
        String kind = jdbc.sql("SELECT kind FROM generation_jobs WHERE id = :id AND owner_id = :owner")
                .param("id", jobId).param("owner", user.id()).query(String.class).optional()
                .orElseThrow(() -> ApiException.notFound("Job"));
        if (!JobKind.valueOf(kind).isGeneration()) {
            throw ApiException.conflict("NOT_A_RESULT", "Only generated results can be deleted one at a time");
        }
        removeJobs(List.of(jobId), user.id());
        record("RESULT", jobId, user.id());
        audit.user(user, "RESULT_DELETED", "job", jobId.toString(), Map.of("kind", kind));
    }

    /** One recorded deletion, after a restore. Returns whether the restored database had brought it back. */
    @Transactional
    public boolean reapply(String kind, UUID targetId, UUID ownerId, UUID recordId) {
        boolean restored = switch (kind) {
            case "PROJECT" -> projectRepository.findById(targetId).filter(project -> "ACTIVE".equals(project.getStatus()))
                    .map(project -> {
                        purgeProject(project);
                        return true;
                    }).orElse(false);
            case "ASSET" -> assets.findById(targetId).filter(asset -> !AssetEntity.DELETED.equals(asset.getStatus()))
                    .map(asset -> {
                        purgeAsset(asset);
                        return true;
                    }).orElse(false);
            case "RESULT" -> {
                boolean exists = jdbc.sql("SELECT count(*) FROM generation_jobs WHERE id = :id").param("id", targetId)
                        .query(Long.class).single() > 0;
                if (exists) {
                    removeJobs(List.of(targetId), ownerId);
                }
                yield exists;
            }
            default -> throw new IllegalArgumentException("unknown deletion kind " + kind);
        };
        // Back in the log too, already exported, so the database again knows what happened.
        jdbc.sql("""
                INSERT INTO deletion_log (id, kind, target_id, owner_id, exported_at) VALUES (:id, :kind, :target, :owner, now())
                ON CONFLICT (id) DO NOTHING
                """).param("id", recordId).param("kind", kind).param("target", targetId).param("owner", ownerId).update();
        return restored;
    }

    private void record(String kind, UUID targetId, UUID ownerId) {
        jdbc.sql("INSERT INTO deletion_log (id, kind, target_id, owner_id) VALUES (:id, :kind, :target, :owner)")
                .param("id", UUID.randomUUID()).param("kind", kind).param("target", targetId).param("owner", ownerId).update();
    }

    /** Attempt rows go with their job, so their storage prefixes are recorded first. By prefix catches unreported output. */
    private void removeJobs(List<UUID> jobIds, UUID ownerId) {
        if (jobIds.isEmpty()) {
            return;
        }
        for (UUID attempt : jdbc.sql("SELECT id FROM job_attempts WHERE job_id IN (:jobs)").param("jobs", jobIds).query(UUID.class).list()) {
            tombstone("attempts/" + attempt + "/", ownerId);
        }
        jdbc.sql("DELETE FROM preprocessing_artifacts WHERE job_id IN (:jobs)").param("jobs", jobIds).update();
        jdbc.sql("DELETE FROM outputs WHERE job_id IN (:jobs)").param("jobs", jobIds).update();
        jdbc.sql("DELETE FROM generation_jobs WHERE id IN (:jobs)").param("jobs", jobIds).update();
    }

    private void tombstoneAsset(AssetEntity asset) {
        tombstone(asset.getUploadKey(), asset.getOwnerId());
        if (asset.getObjectKey() != null) {
            tombstone(asset.getObjectKey(), asset.getOwnerId());
        }
        // A validated photo's object key moves to its normalized copy, and the original stays under this prefix.
        tombstone("assets/" + asset.getOwnerId() + "/" + asset.getProjectId() + "/" + asset.getId() + "/", asset.getOwnerId());
    }

    private void tombstone(String key, UUID ownerId) {
        jdbc.sql("""
                INSERT INTO deletion_tombstones (object_key, owner_id) VALUES (:key, :owner)
                ON CONFLICT (object_key) DO UPDATE SET purged_at = NULL, deleted_at = now()
                """).param("key", key).param("owner", ownerId).update();
    }
}
