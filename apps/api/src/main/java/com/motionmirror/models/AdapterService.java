package com.motionmirror.models;

import com.motionmirror.assets.ObjectStorage;
import com.motionmirror.audit.AuditService;
import com.motionmirror.cache.MetadataCache;
import com.motionmirror.config.AppProperties;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.shared.ApiException;
import com.motionmirror.shared.Json;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.JsonNode;

/**
 * The trained-adapter lifecycle: REGISTERED, EVALUATED, STAGED, PROMOTED, with DEPRECATED and rollback. Promotion
 * creates a new preset revision, so jobs already running keep the configuration they started with.
 */
@Service
public class AdapterService {

    public record NewAdapter(UUID id, String name, String baseModelId, String baseRevision, String sha256,
            List<String> targetModules, int rank, double scale, String license, String licenseScope,
            JsonNode datasetVersions, JsonNode trainedOn, JsonNode evalManifest) {}

    public record Adapter(UUID id, String name, String baseModelId, String baseRevision, String artifactKey, String sha256,
            int rank, double scale, String license, String licenseScope, JsonNode datasetVersions, JsonNode trainedOn,
            JsonNode evalManifest, String state, Instant createdAt) {}

    public record UploadTarget(UUID id, String key, String url) {}

    private static final Set<String> SCOPES = Set.of("research_only", "production");

    private final JdbcClient jdbc;
    private final ObjectStorage storage;
    private final AuditService audit;
    private final MetadataCache cache;
    private final AppProperties.Model model;

    public AdapterService(JdbcClient jdbc, ObjectStorage storage, AuditService audit, MetadataCache cache, AppProperties properties) {
        this.jdbc = jdbc;
        this.storage = storage;
        this.audit = audit;
        this.cache = cache;
        this.model = properties.model();
    }

    /** The key is chosen here and is immutable. Nobody can register a path or URL of their own. */
    public UploadTarget uploadTarget(CurrentUser admin) {
        UUID id = UUID.randomUUID();
        String key = artifactKey(id);
        audit.user(admin, "ADAPTER_UPLOAD_TARGET", "adapter", id.toString(), Map.of());
        return new UploadTarget(id, key, storage.presignAdminWrite(key));
    }

    private static String artifactKey(UUID id) {
        return "adapters/" + id + "/adapter.safetensors";
    }

    @Transactional
    public Adapter register(CurrentUser admin, NewAdapter request) {
        if (!model.baseId().equals(request.baseModelId()) || !model.baseRevision().equals(request.baseRevision())) {
            throw ApiException.conflict("BASE_REVISION_MISMATCH",
                    "An adapter only works with the base it was trained on. This deployment serves " + model.baseId() + " at " + model.baseRevision());
        }
        if (!SCOPES.contains(request.licenseScope())) {
            throw ApiException.badRequest("UNKNOWN_LICENSE_SCOPE", "Licence scope must be research_only or production");
        }
        String key = artifactKey(request.id());
        if (storage.head(key).isEmpty()) {
            throw ApiException.conflict("ARTIFACT_NOT_UPLOADED", "Upload the safetensors file first");
        }
        String actual = storage.sha256(key);
        if (!actual.equals(request.sha256())) {
            throw ApiException.conflict("HASH_MISMATCH", "The uploaded file doesn't match the hash in the run manifest");
        }
        String state = request.evalManifest() == null ? "REGISTERED" : "EVALUATED";
        if (request.evalManifest() != null) {
            requireProductionClassEvaluation(request.evalManifest());
        }
        jdbc.sql("""
                INSERT INTO model_adapters (id, name, base_model_id, base_revision, artifact_key, sha256, target_modules, rank, scale,
                    license, license_scope, dataset_versions, trained_on, eval_manifest, state, registered_by)
                VALUES (:id, :name, :base, :revision, :key, :sha, CAST(:targets AS jsonb), :rank, :scale, :license, :scope,
                    CAST(:datasets AS jsonb), CAST(:trained AS jsonb), CAST(:eval AS jsonb), :state, :by)
                """)
                .param("id", request.id()).param("name", request.name()).param("base", request.baseModelId())
                .param("revision", request.baseRevision()).param("key", key).param("sha", actual)
                .param("targets", Json.write(request.targetModules())).param("rank", request.rank()).param("scale", request.scale())
                .param("license", request.license()).param("scope", request.licenseScope())
                .param("datasets", String.valueOf(request.datasetVersions())).param("trained", String.valueOf(request.trainedOn()))
                .param("eval", request.evalManifest() == null ? null : request.evalManifest().toString())
                .param("state", state).param("by", admin.id())
                .update();
        audit.user(admin, "ADAPTER_REGISTERED", "adapter", request.id().toString(),
                Map.of("sha256", actual, "license_scope", request.licenseScope()));
        return require(request.id());
    }

    /** Numerics differ across devices, so only a CUDA evaluation through the worker path counts. */
    private static void requireProductionClassEvaluation(JsonNode manifest) {
        if (!"cuda".equals(manifest.path("hardware").path("device_type").asString(""))) {
            throw ApiException.conflict("EVALUATION_NOT_PRODUCTION_CLASS", "The evaluation must run on a production-class CUDA worker");
        }
        if (!manifest.path("human_review").path("passed").asBoolean(false)) {
            throw ApiException.conflict("EVALUATION_NOT_PASSED", "The evaluation has no passing human review");
        }
    }

    @Transactional
    public Adapter attachEvaluation(CurrentUser admin, UUID id, JsonNode manifest) {
        Adapter adapter = requireLocked(id);
        requireProductionClassEvaluation(manifest);
        if (!Set.of("REGISTERED", "EVALUATED").contains(adapter.state())) {
            throw ApiException.conflict("ADAPTER_WRONG_STATE", "This adapter has already moved past evaluation");
        }
        jdbc.sql("UPDATE model_adapters SET eval_manifest = CAST(:eval AS jsonb), state = 'EVALUATED', updated_at = now() WHERE id = :id")
                .param("eval", manifest.toString()).param("id", id).update();
        audit.user(admin, "ADAPTER_EVALUATED", "adapter", id.toString(), Map.of());
        return require(id);
    }

    /** A new revision only administrators can see, so a canary can run without changing anyone's default. */
    @Transactional
    public int stage(CurrentUser admin, UUID id, String presetId) {
        Adapter adapter = requireLocked(id);
        if (adapter.evalManifest() == null) {
            throw ApiException.conflict("EVALUATION_MISSING", "Evaluate the adapter on a production-class worker before staging it");
        }
        if (!Set.of("EVALUATED", "STAGED", "PROMOTED").contains(adapter.state())) {
            throw ApiException.conflict("ADAPTER_WRONG_STATE", "A deprecated adapter can't be staged");
        }
        int revision = newRevision(presetId, adapter, "ADMIN", admin);
        // Staging again for another canary doesn't demote an adapter that is already the default somewhere.
        jdbc.sql("UPDATE model_adapters SET state = 'STAGED', updated_at = now() WHERE id = :id AND state = 'EVALUATED'").param("id", id).update();
        recordPromotion(presetId, revision, currentRevision(presetId), id, "STAGE", admin);
        audit.user(admin, "ADAPTER_STAGED", "adapter", id.toString(), Map.of("preset", presetId + "@" + revision));
        evictPresets();
        return revision;
    }

    /** Research-only data can never reach the production default. This is the licence decision, enforced in code. */
    @Transactional
    public int promote(CurrentUser admin, UUID id, String presetId) {
        Adapter adapter = requireLocked(id);
        if ("research_only".equals(adapter.licenseScope())) {
            throw new ApiException(HttpStatus.CONFLICT, "RESEARCH_ADAPTER",
                    "This adapter was trained on research-only data and can't be promoted to production");
        }
        if (!"STAGED".equals(adapter.state())) {
            throw ApiException.conflict("ADAPTER_NOT_STAGED", "Stage the adapter and review a canary run before promoting it");
        }
        int previous = currentRevision(presetId);
        int revision = newRevision(presetId, adapter, "PUBLIC", admin);
        movePresetHead(presetId, revision);
        jdbc.sql("UPDATE model_adapters SET state = 'PROMOTED', updated_at = now() WHERE id = :id").param("id", id).update();
        recordPromotion(presetId, revision, previous, id, "PROMOTE", admin);
        audit.user(admin, "ADAPTER_PROMOTED", "adapter", id.toString(), Map.of("preset", presetId + "@" + revision, "sha256", adapter.sha256()));
        evictPresets();
        return revision;
    }

    /** Repoints the default. No redeploy, and running jobs keep their snapshot. */
    @Transactional
    public int rollback(CurrentUser admin, String presetId) {
        Integer current = currentRevision(presetId);
        Integer previous = jdbc.sql("""
                SELECT previous_revision FROM adapter_promotions
                WHERE preset_id = :preset AND preset_revision = :revision AND action = 'PROMOTE'
                ORDER BY created_at DESC LIMIT 1
                """).param("preset", presetId).param("revision", current).query(Integer.class).optional().orElse(null);
        if (previous == null) {
            throw ApiException.conflict("NOTHING_TO_ROLL_BACK", "This preset has no earlier revision to return to");
        }
        movePresetHead(presetId, previous);
        // No longer the default, so it can be promoted again after whatever went wrong is fixed.
        jdbc.sql("""
                UPDATE model_adapters SET state = 'STAGED', updated_at = now()
                WHERE id = (SELECT adapter_id FROM presets WHERE id = :preset AND revision = :revision) AND state = 'PROMOTED'
                """).param("preset", presetId).param("revision", current).update();
        recordPromotion(presetId, previous, current, null, "ROLLBACK", admin);
        audit.user(admin, "PRESET_ROLLED_BACK", "preset", presetId, Map.of("from", current, "to", previous));
        evictPresets();
        return previous;
    }

    @Transactional
    public Adapter deprecate(CurrentUser admin, UUID id) {
        requireLocked(id);
        Integer asDefault = jdbc.sql("""
                SELECT count(*) FROM preset_heads h JOIN presets p ON p.id = h.id AND p.revision = h.current_revision
                WHERE p.adapter_id = :id
                """).param("id", id).query(Integer.class).single();
        if (asDefault > 0) {
            throw ApiException.conflict("ADAPTER_IN_USE", "This adapter is a preset's default. Roll the preset back first");
        }
        jdbc.sql("UPDATE model_adapters SET state = 'DEPRECATED', updated_at = now() WHERE id = :id").param("id", id).update();
        audit.user(admin, "ADAPTER_DEPRECATED", "adapter", id.toString(), Map.of());
        return require(id);
    }

    public List<Adapter> list() {
        return jdbc.sql(SELECT + " ORDER BY created_at DESC LIMIT 200").query(AdapterService::map).list();
    }

    public Adapter require(UUID id) {
        return jdbc.sql(SELECT + " WHERE id = :id").param("id", id).query(AdapterService::map).optional()
                .orElseThrow(() -> ApiException.notFound("Adapter"));
    }

    private Adapter requireLocked(UUID id) {
        jdbc.sql("SELECT id FROM model_adapters WHERE id = :id FOR UPDATE").param("id", id).query(UUID.class).optional()
                .orElseThrow(() -> ApiException.notFound("Adapter"));
        return require(id);
    }

    private int currentRevision(String presetId) {
        return jdbc.sql("SELECT current_revision FROM preset_heads WHERE id = :id").param("id", presetId).query(Integer.class).optional()
                .orElseThrow(() -> ApiException.notFound("Preset"));
    }

    /** Revisions are immutable, so every change is a copy with the adapter recorded in it. */
    private int newRevision(String presetId, Adapter adapter, String visibility, CurrentUser admin) {
        int revision = jdbc.sql("SELECT COALESCE(max(revision), 0) + 1 FROM presets WHERE id = :id").param("id", presetId).query(Integer.class).single();
        Map<String, Object> reference = Map.of("id", adapter.id().toString(), "name", adapter.name(), "sha256", adapter.sha256(),
                "scale", adapter.scale(), "license_scope", adapter.licenseScope());
        int inserted = jdbc.sql("""
                INSERT INTO presets (id, revision, display_name, backend_id, definition, adapter_id, visibility, created_by)
                SELECT p.id, :revision, p.display_name, p.backend_id,
                       jsonb_set(jsonb_set(p.definition, '{revision}', to_jsonb(:revision::int)), '{adapter}', CAST(:adapter AS jsonb)),
                       :adapterId, :visibility, :by
                FROM presets p JOIN preset_heads h ON h.id = p.id AND h.current_revision = p.revision
                WHERE p.id = :id
                """)
                .param("revision", revision).param("adapter", Json.write(reference)).param("adapterId", adapter.id())
                .param("visibility", visibility).param("by", admin.id()).param("id", presetId).update();
        if (inserted == 0) {
            throw ApiException.notFound("Preset");
        }
        return revision;
    }

    private void movePresetHead(String presetId, int revision) {
        jdbc.sql("UPDATE preset_heads SET current_revision = :revision, updated_at = now() WHERE id = :id")
                .param("revision", revision).param("id", presetId).update();
    }

    private void recordPromotion(String presetId, int revision, Integer previous, UUID adapter, String action, CurrentUser admin) {
        jdbc.sql("""
                INSERT INTO adapter_promotions (id, preset_id, preset_revision, previous_revision, adapter_id, action, actor_id)
                VALUES (:id, :preset, :revision, :previous, :adapter, :action, :actor)
                """)
                .param("id", UUID.randomUUID()).param("preset", presetId).param("revision", revision)
                .param("previous", previous).param("adapter", adapter).param("action", action).param("actor", admin.id()).update();
    }

    /** After commit, or a request in between could put the old list straight back for the whole TTL. */
    private void evictPresets() {
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                cache.evict("presets:admin");
                cache.evict("presets:public");
            }
        });
    }

    private static final String SELECT = """
            SELECT id, name, base_model_id, base_revision, artifact_key, sha256, rank, scale, license, license_scope,
                   dataset_versions::text AS dataset_versions, trained_on::text AS trained_on, eval_manifest::text AS eval_manifest,
                   state, created_at FROM model_adapters
            """;

    private static Adapter map(java.sql.ResultSet row, int index) throws java.sql.SQLException {
        String eval = row.getString("eval_manifest");
        return new Adapter(row.getObject("id", UUID.class), row.getString("name"), row.getString("base_model_id"),
                row.getString("base_revision"), row.getString("artifact_key"), row.getString("sha256"), row.getInt("rank"),
                row.getDouble("scale"), row.getString("license"), row.getString("license_scope"),
                Json.read(row.getString("dataset_versions")), Json.read(row.getString("trained_on")),
                eval == null ? null : Json.read(eval), row.getString("state"),
                row.getObject("created_at", OffsetDateTime.class).toInstant());
    }
}
