package com.motionmirror.workers;

import com.motionmirror.assets.AssetEntity;
import com.motionmirror.assets.AssetRepository;
import com.motionmirror.jobs.JobKind;
import com.motionmirror.jobs.JobRow;
import com.motionmirror.jobs.JobStore;
import com.motionmirror.shared.ApiException;
import com.motionmirror.shared.Json;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/** What a finished job means for the things it worked on, and which inputs a job's lease may read. */
@Component
public class JobCompletionHandlers {

    private static final String NORMALIZED_IMAGE = "normalized.png";

    private final AssetRepository assets;
    private final JobStore jobs;
    private final JdbcClient jdbc;

    public JobCompletionHandlers(AssetRepository assets, JobStore jobs, JdbcClient jdbc) {
        this.assets = assets;
        this.jobs = jobs;
        this.jdbc = jdbc;
    }

    void onSucceeded(JobRow job, WorkerDtos.CompleteRequest request) {
        if (job.kind() == JobKind.PREPROCESS) {
            rememberPreprocessing(job, request);
            return;
        }
        if (job.kind() != JobKind.VALIDATE) {
            return;
        }
        AssetEntity asset = assets.findById(UUID.fromString(Json.read(job.inputs()).get("asset_id").asString())).orElseThrow();
        Map<String, Object> result = request.result() == null ? Map.of() : request.result();
        // A photo may have been normalized by the worker (EXIF removed, orientation applied).
        String key = request.outputs().stream().filter(output -> output.name().equals(NORMALIZED_IMAGE)).findFirst()
                .map(WorkerDtos.OutputRef::key).orElse(asset.getObjectKey());
        long size = request.outputs().stream().filter(output -> output.name().equals(NORMALIZED_IMAGE)).findFirst()
                .map(WorkerDtos.OutputRef::sizeBytes).orElse(asset.getSizeBytes());
        asset.markReady(key, size, String.valueOf(result.get("sha256")), String.valueOf(result.get("detected_type")),
                Json.write(result.getOrDefault("media", Map.of())));
        assets.save(asset);
    }

    private void rememberPreprocessing(JobRow job, WorkerDtos.CompleteRequest request) {
        JsonNode inputs = Json.read(job.inputs());
        Object toolRevision = request.result() == null ? null : request.result().get("tool_revision");
        jdbc.sql("""
                INSERT INTO preprocessing_artifacts (id, project_id, job_id, cache_key, source_asset_id, selection_id, config, tool_revision)
                VALUES (:id, :project, :job, :key, :source, :selection, CAST(:config AS jsonb), :tool)
                ON CONFLICT (cache_key) DO NOTHING
                """)
                .param("id", UUID.randomUUID()).param("project", job.projectId()).param("job", job.id())
                .param("key", inputs.path("cache_key").asString())
                .param("source", UUID.fromString(inputs.path("source_asset_id").asString()))
                .param("selection", UUID.fromString(inputs.path("selection_id").asString()))
                .param("config", job.effectiveSettings())
                .param("tool", toolRevision == null ? "unknown" : toolRevision.toString())
                .update();
    }

    void onFailed(JobRow job, String code, String detail) {
        if (job.kind() != JobKind.VALIDATE) {
            return;
        }
        assets.findById(UUID.fromString(Json.read(job.inputs()).get("asset_id").asString())).ifPresent(asset -> {
            asset.reject(code, detail);
            assets.save(asset);
        });
    }

    /** Named inputs only: a worker can't ask for an arbitrary key. */
    String resolveReadKey(JobRow job, String name) {
        JsonNode inputs = Json.read(job.inputs());
        if (name.startsWith("preprocess:")) {
            UUID preprocessJob = UUID.fromString(required(inputs, "preprocess_job_id"));
            String output = name.substring("preprocess:".length());
            return jobs.outputs(preprocessJob).stream().filter(row -> row.name().equals(output)).findFirst()
                    .map(JobStore.OutputRow::objectKey)
                    .orElseThrow(() -> ApiException.badRequest("UNKNOWN_INPUT", "No such preprocessing output"));
        }
        String assetId = switch (name) {
            case "asset" -> required(inputs, "asset_id");
            case "source" -> required(inputs, "source_asset_id");
            case "primary_reference" -> required(inputs, "primary_reference_asset_id");
            default -> throw ApiException.badRequest("UNKNOWN_INPUT", "Unknown input " + name);
        };
        AssetEntity asset = assets.findById(UUID.fromString(assetId)).orElseThrow(() -> ApiException.badRequest("UNKNOWN_INPUT", "Input is gone"));
        if (asset.getObjectKey() == null || AssetEntity.DELETED.equals(asset.getStatus())) {
            throw ApiException.badRequest("UNKNOWN_INPUT", "Input is gone");
        }
        return asset.getObjectKey();
    }

    private static String required(JsonNode inputs, String field) {
        JsonNode value = inputs.get(field);
        if (value == null || value.isNull()) {
            throw ApiException.badRequest("UNKNOWN_INPUT", "This job has no " + field);
        }
        return value.asString();
    }
}
