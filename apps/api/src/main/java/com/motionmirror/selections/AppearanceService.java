package com.motionmirror.selections;

import com.motionmirror.assets.AssetEntity;
import com.motionmirror.assets.AssetService;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.projects.ProjectService;
import com.motionmirror.shared.ApiException;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Target appearance. One primary full-body photo is what the model consumes. Other views are stored and shown
 * as supplementary, because a list of photos is not multi-view conditioning.
 */
@Service
public class AppearanceService {

    private static final Set<String> VIEW_LABELS = Set.of("FULL_BODY", "FRONT", "BACK", "SIDE", "THREE_QUARTER", "FACE");

    public record ReferenceInput(UUID assetId, String viewLabel, boolean primary) {}

    public record Reference(UUID assetId, String viewLabel, boolean primary) {}

    public record Profile(UUID id, UUID projectId, String name, List<Reference> references) {

        public Reference primary() {
            return references.stream().filter(Reference::primary).findFirst().orElseThrow();
        }

        public List<Reference> supplementary() {
            return references.stream().filter(reference -> !reference.primary()).toList();
        }
    }

    private final JdbcClient jdbc;
    private final ProjectService projects;
    private final AssetService assets;

    public AppearanceService(JdbcClient jdbc, ProjectService projects, AssetService assets) {
        this.jdbc = jdbc;
        this.projects = projects;
        this.assets = assets;
    }

    @Transactional
    public Profile create(CurrentUser user, UUID projectId, String name, List<ReferenceInput> references) {
        projects.requireOwned(user, projectId);
        if (references == null || references.isEmpty()) {
            throw ApiException.badRequest("PRIMARY_REQUIRED", "Choose one full-body photo as the primary reference");
        }
        List<ReferenceInput> primaries = references.stream().filter(ReferenceInput::primary).toList();
        if (primaries.size() != 1) {
            throw ApiException.badRequest("PRIMARY_REQUIRED", "Choose exactly one primary reference");
        }
        if ("FACE".equals(primaries.getFirst().viewLabel())) {
            throw ApiException.badRequest("PRIMARY_MUST_SHOW_BODY", "A face close-up can't specify an outfit. Choose a full-body photo as primary");
        }
        for (ReferenceInput reference : references) {
            if (!VIEW_LABELS.contains(reference.viewLabel())) {
                throw ApiException.badRequest("UNKNOWN_VIEW_LABEL", "Unknown view label");
            }
            assets.requireUsable(user, projectId, reference.assetId(), AssetEntity.TARGET_REFERENCE);
        }
        UUID id = UUID.randomUUID();
        jdbc.sql("INSERT INTO appearance_profiles (id, project_id, owner_id, name) VALUES (:id, :project, :owner, :name)")
                .param("id", id).param("project", projectId).param("owner", user.id()).param("name", name).update();
        for (ReferenceInput reference : references) {
            jdbc.sql("""
                    INSERT INTO reference_images (id, profile_id, asset_id, view_label, is_primary)
                    VALUES (:id, :profile, :asset, :label, :primary)
                    """)
                    .param("id", UUID.randomUUID()).param("profile", id).param("asset", reference.assetId())
                    .param("label", reference.viewLabel()).param("primary", reference.primary()).update();
        }
        return requireOwned(user, projectId, id);
    }

    @Transactional(readOnly = true)
    public Profile requireOwned(CurrentUser user, UUID projectId, UUID profileId) {
        String name = jdbc.sql("SELECT name FROM appearance_profiles WHERE id = :id AND project_id = :project AND owner_id = :owner")
                .param("id", profileId).param("project", projectId).param("owner", user.id()).query(String.class)
                .optional().orElseThrow(() -> ApiException.notFound("Appearance profile"));
        return new Profile(profileId, projectId, name, references(profileId));
    }

    @Transactional(readOnly = true)
    public List<Profile> list(CurrentUser user, UUID projectId) {
        projects.requireOwned(user, projectId);
        return jdbc.sql("SELECT id, name FROM appearance_profiles WHERE project_id = :project AND owner_id = :owner ORDER BY created_at DESC")
                .param("project", projectId).param("owner", user.id())
                .query((row, index) -> new Profile(row.getObject("id", UUID.class), projectId, row.getString("name"),
                        references(row.getObject("id", UUID.class))))
                .list();
    }

    private List<Reference> references(UUID profileId) {
        return jdbc.sql("SELECT asset_id, view_label, is_primary FROM reference_images WHERE profile_id = :profile ORDER BY is_primary DESC, created_at")
                .param("profile", profileId)
                .query((row, index) -> new Reference(row.getObject("asset_id", UUID.class), row.getString("view_label"), row.getBoolean("is_primary")))
                .list();
    }
}
