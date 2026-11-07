package com.motionmirror.jobs;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.motionmirror.assets.AssetEntity;
import com.motionmirror.assets.AssetService;
import com.motionmirror.audit.AuditService;
import com.motionmirror.cache.RateLimiter;
import com.motionmirror.config.AppProperties;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.presets.PresetService;
import com.motionmirror.presets.SettingsValidator;
import com.motionmirror.projects.ProjectService;
import com.motionmirror.selections.AppearanceService;
import com.motionmirror.selections.SelectionService;
import com.motionmirror.shared.ApiException;
import com.motionmirror.shared.Json;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Turns user requests into jobs with immutable configuration snapshots. */
@Service
public class JobSubmissionService {

    private final JobService jobs;
    private final JobStore store;
    private final ProjectService projects;
    private final AssetService assets;
    private final SelectionService selections;
    private final AppearanceService appearance;
    private final PresetService presets;
    private final RateLimiter limiter;
    private final AuditService audit;
    private final JobRouting routing;
    private final JdbcClient jdbc;
    private final AppProperties properties;

    public JobSubmissionService(JobService jobs, JobStore store, ProjectService projects, AssetService assets,
            SelectionService selections, AppearanceService appearance, PresetService presets, RateLimiter limiter,
            AuditService audit, JobRouting routing, JdbcClient jdbc, AppProperties properties) {
        this.jobs = jobs;
        this.store = store;
        this.projects = projects;
        this.assets = assets;
        this.selections = selections;
        this.appearance = appearance;
        this.presets = presets;
        this.limiter = limiter;
        this.audit = audit;
        this.routing = routing;
        this.jdbc = jdbc;
        this.properties = properties;
    }

    /** Masks are reused while the source, selection and tools are unchanged. A new photo or prompt never redoes them. */
    @Transactional
    public JobRow submitPreprocess(CurrentUser user, UUID projectId, UUID selectionId, String idempotencyKey) {
        projects.requireOwned(user, projectId);
        SelectionService.Selection selection = selections.requireOwned(user, projectId, selectionId);
        AssetEntity source = assets.requireUsable(user, projectId, selection.sourceAssetId(), AssetEntity.DRIVING_VIDEO);
        limiter.check("submit", user.id(), properties.limits().submissionsPerMinute());

        PresetService.Preset preset = presets.requireCurrent("standard", user.isAdmin());
        JsonNode settings = preset.settings();
        JobRouting.Route route = routing.routeFor(JobKind.PREPROCESS);
        String cacheKey = sha256(String.join("|", source.getChecksumSha256(), selection.id().toString(), route.backendId(),
                settings.path("processing_fps").asText(), settings.path("mask_expand_pixels").asText(),
                settings.path("mask_feather_pixels").asText()));
        Optional<UUID> cached = jdbc.sql("""
                SELECT a.job_id FROM preprocessing_artifacts a JOIN generation_jobs j ON j.id = a.job_id
                WHERE a.cache_key = :key AND j.state = 'SUCCEEDED' AND j.owner_id = :owner
                """).param("key", cacheKey).param("owner", user.id()).query(UUID.class).optional();
        if (cached.isPresent()) {
            return store.find(cached.get()).orElseThrow();
        }

        Map<String, Object> inputs = new LinkedHashMap<>();
        inputs.put("source_asset_id", source.getId().toString());
        inputs.put("selection_id", selection.id().toString());
        inputs.put("selection_revision", selection.revision());
        inputs.put("frame_index", selection.frameIndex());
        inputs.put("timestamp_num", selection.timestampNum());
        inputs.put("timestamp_den", selection.timestampDen());
        inputs.put("annotations", selection.annotations());
        inputs.put("coordinate_mapping", selection.coordinateMapping());
        inputs.put("trim_start_seconds", selection.trimStartSeconds());
        inputs.put("trim_end_seconds", selection.trimEndSeconds());
        inputs.put("cache_key", cacheKey);
        return jobs.submit(user.id(), new JobService.Submission(projectId, JobKind.PREPROCESS, preset.configRevision(),
                "{}", settings.toString(), Json.write(inputs), idempotencyKey, true));
    }

    public record GenerationRequest(JobKind kind, UUID selectionId, UUID appearanceProfileId, UUID preprocessJobId,
            String presetId, String overridesJson, boolean consentConfirmed, String idempotencyKey) {}

    @Transactional
    public JobRow submitGeneration(CurrentUser user, UUID projectId, GenerationRequest request) {
        projects.requireOwned(user, projectId);
        if (!request.consentConfirmed()) {
            throw ApiException.badRequest("CONSENT_REQUIRED", "Confirm you have permission to use this person's likeness");
        }
        if (!request.kind().isGeneration()) {
            throw ApiException.badRequest("UNKNOWN_JOB_KIND", "Choose a preview or a full generation");
        }
        SelectionService.Selection selection = selections.requireOwned(user, projectId, request.selectionId());
        AssetEntity source = assets.requireUsable(user, projectId, selection.sourceAssetId(), AssetEntity.DRIVING_VIDEO);
        AppearanceService.Profile profile = appearance.requireOwned(user, projectId, request.appearanceProfileId());
        AssetEntity primary = assets.requireUsable(user, projectId, profile.primary().assetId(), AssetEntity.TARGET_REFERENCE);
        JobRow preprocess = store.findOwned(request.preprocessJobId(), user.id())
                .filter(job -> job.projectId().equals(projectId))
                .orElseThrow(() -> ApiException.notFound("Preprocessing job"));
        if (preprocess.kind() != JobKind.PREPROCESS || preprocess.state() != JobState.SUCCEEDED) {
            throw ApiException.conflict("PREPROCESS_NOT_READY", "Finish the mask and pose step first");
        }
        String preprocessSelection = Json.read(preprocess.inputs()).path("selection_id").asString();
        if (!selection.id().toString().equals(preprocessSelection)) {
            throw ApiException.conflict("PREPROCESS_STALE", "The selection changed. Run the mask and pose step again");
        }

        limiter.check("submit", user.id(), properties.limits().submissionsPerMinute());
        PresetService.Preset preset = presets.requireCurrent(request.presetId(), user.isAdmin());
        JsonNode overrides = request.overridesJson() == null ? null : SettingsValidator.parse(request.overridesJson());
        ObjectNode effective = presets.resolve(preset, overrides, isPortrait(source));
        checkClipLength(request.kind(), preset, effective, source);

        Map<String, Object> inputs = new LinkedHashMap<>();
        inputs.put("source_asset_id", source.getId().toString());
        inputs.put("selection_id", selection.id().toString());
        inputs.put("preprocess_job_id", preprocess.id().toString());
        inputs.put("primary_reference_asset_id", primary.getId().toString());
        inputs.put("supplementary_reference_asset_ids", profile.supplementary().stream().map(reference -> reference.assetId().toString()).toList());
        inputs.put("source_sha256", source.getChecksumSha256());
        inputs.put("primary_reference_sha256", primary.getChecksumSha256());
        inputs.put("trim_start_seconds", selection.trimStartSeconds());
        inputs.put("trim_end_seconds", selection.trimEndSeconds());
        inputs.put("preview_seconds", request.kind() == JobKind.PREVIEW ? preset.definition().path("preview_seconds").asInt(5) : null);

        JobRow job = jobs.submit(user.id(), new JobService.Submission(projectId, request.kind(), preset.configRevision(),
                overrides == null ? "{}" : overrides.toString(), effective.toString(), Json.write(inputs),
                request.idempotencyKey(), true));
        audit.user(user, "GENERATION_SUBMITTED", "job", job.id().toString(), Map.of("kind", request.kind().name(), "preset", preset.configRevision()));
        return job;
    }

    private static boolean isPortrait(AssetEntity source) {
        if (source.getMedia() == null) {
            return false;
        }
        var media = Json.read(source.getMedia());
        return media.path("display_height").asInt() > media.path("display_width").asInt();
    }

    /** Rejects a clip the pinned pipeline would crash on, before any GPU time is spent. */
    private void checkClipLength(JobKind kind, PresetService.Preset preset, ObjectNode settings, AssetEntity source) {
        if (source.getMedia() == null) {
            return;
        }
        double duration = Json.read(source.getMedia()).path("duration_seconds").asDouble(0);
        if (kind == JobKind.PREVIEW) {
            duration = Math.min(duration, preset.definition().path("preview_seconds").asDouble(5));
        }
        int frames = (int) Math.floor(duration * settings.path("processing_fps").asInt(30));
        if (frames <= settings.path("prev_segment_conditioning_frames").asInt()) {
            throw ApiException.badRequest("CLIP_TOO_SHORT", "This clip is too short for the selected preset");
        }
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }

    public List<JobRow> list(CurrentUser user, UUID projectId, int page, int size) {
        projects.requireOwned(user, projectId);
        return jobs.listForProject(projectId, page, size);
    }
}
