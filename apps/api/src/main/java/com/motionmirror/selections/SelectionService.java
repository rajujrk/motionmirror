package com.motionmirror.selections;

import com.motionmirror.assets.AssetEntity;
import com.motionmirror.assets.AssetService;
import com.motionmirror.config.AppProperties;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.projects.ProjectService;
import com.motionmirror.shared.ApiException;
import com.motionmirror.shared.Json;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;

/** Which person to replace. A correction is a new revision, so earlier results stay explainable. */
@Service
public class SelectionService {

    public static final int MAX_ANNOTATION_BYTES = 64 * 1024;

    public record NewSelection(UUID sourceAssetId, int frameIndex, long timestampNum, long timestampDen,
            JsonNode coordinateMapping, JsonNode annotations, Double trimStartSeconds, Double trimEndSeconds) {}

    public record Selection(UUID id, UUID projectId, UUID sourceAssetId, int revision, int frameIndex, long timestampNum,
            long timestampDen, JsonNode coordinateMapping, JsonNode annotations, Double trimStartSeconds,
            Double trimEndSeconds, Instant createdAt) {}

    private final JdbcClient jdbc;
    private final ProjectService projects;
    private final AssetService assets;
    private final AppProperties properties;

    public SelectionService(JdbcClient jdbc, ProjectService projects, AssetService assets, AppProperties properties) {
        this.jdbc = jdbc;
        this.projects = projects;
        this.assets = assets;
        this.properties = properties;
    }

    @Transactional
    public Selection create(CurrentUser user, UUID projectId, NewSelection request) {
        projects.requireOwned(user, projectId);
        AssetEntity source = assets.requireUsable(user, projectId, request.sourceAssetId(), AssetEntity.DRIVING_VIDEO);
        validateAnnotations(request.annotations());
        validateTrim(request);
        validateAgainstClip(request, source);

        // Row lock on the asset serializes revision numbers for one source video.
        jdbc.sql("SELECT id FROM assets WHERE id = :id FOR UPDATE").param("id", request.sourceAssetId()).query(UUID.class).single();
        int revision = jdbc.sql("SELECT COALESCE(max(revision), 0) + 1 FROM subject_selections WHERE source_asset_id = :asset")
                .param("asset", request.sourceAssetId()).query(Integer.class).single();
        UUID id = UUID.randomUUID();
        jdbc.sql("""
                INSERT INTO subject_selections (id, project_id, owner_id, source_asset_id, revision, frame_index, timestamp_num,
                    timestamp_den, coordinate_mapping, annotations, trim_start_seconds, trim_end_seconds)
                VALUES (:id, :project, :owner, :asset, :revision, :frame, :num, :den, CAST(:mapping AS jsonb), CAST(:annotations AS jsonb), :trimStart, :trimEnd)
                """)
                .param("id", id).param("project", projectId).param("owner", user.id()).param("asset", request.sourceAssetId())
                .param("revision", revision).param("frame", request.frameIndex()).param("num", request.timestampNum())
                .param("den", request.timestampDen()).param("mapping", request.coordinateMapping().toString())
                .param("annotations", request.annotations().toString()).param("trimStart", request.trimStartSeconds())
                .param("trimEnd", request.trimEndSeconds())
                .update();
        return requireOwned(user, projectId, id);
    }

    private static void validateAnnotations(JsonNode annotations) {
        if (annotations == null || !annotations.isObject() || annotations.toString().length() > MAX_ANNOTATION_BYTES) {
            throw ApiException.badRequest("INVALID_ANNOTATIONS", "Selection annotations are invalid or too large");
        }
        boolean hasPositivePoint = false;
        JsonNode points = annotations.path("points");
        if (points.isArray()) {
            for (JsonNode point : points) {
                if (!point.path("x").isNumber() || !point.path("y").isNumber()) {
                    throw ApiException.badRequest("INVALID_ANNOTATIONS", "Every point needs x and y");
                }
                hasPositivePoint |= point.path("positive").asBoolean(true);
            }
        }
        boolean hasBox = annotations.path("box").isObject();
        if (!hasPositivePoint && !hasBox) {
            throw ApiException.badRequest("SELECTION_EMPTY", "Mark the dancer with a point or a box");
        }
    }

    private static void validateTrim(NewSelection request) {
        Double start = request.trimStartSeconds();
        Double end = request.trimEndSeconds();
        if (start != null && end != null && end <= start) {
            throw ApiException.badRequest("INVALID_TRIM", "The trim end must be after its start");
        }
    }

    /** The selected range must fit the limit and be one continuous shot, so tracking never crosses a cut. */
    private void validateAgainstClip(NewSelection request, AssetEntity source) {
        if (source.getMedia() == null) {
            return;
        }
        JsonNode media = Json.read(source.getMedia());
        double duration = media.path("duration_seconds").asDouble(0);
        double start = request.trimStartSeconds() == null ? 0 : request.trimStartSeconds();
        double end = request.trimEndSeconds() == null ? duration : request.trimEndSeconds();
        int limit = properties.limits().maxSourceSeconds();
        if (end - start > limit) {
            throw ApiException.badRequest("TRIM_REQUIRED", "Trim the clip to " + limit + " seconds or less");
        }
        for (JsonNode cut : media.path("cuts")) {
            double at = cut.asDouble();
            if (at > start && at < end) {
                throw ApiException.badRequest("MULTIPLE_SHOTS",
                        "This part of the clip has a cut at " + String.format(java.util.Locale.ROOT, "%.2f", at)
                                + " seconds. Trim to one continuous shot");
            }
        }
    }

    @Transactional(readOnly = true)
    public Selection requireOwned(CurrentUser user, UUID projectId, UUID selectionId) {
        return jdbc.sql(BASE + " WHERE id = :id AND project_id = :project AND owner_id = :owner")
                .param("id", selectionId).param("project", projectId).param("owner", user.id())
                .query(SelectionService::map).optional().orElseThrow(() -> ApiException.notFound("Selection"));
    }

    @Transactional(readOnly = true)
    public List<Selection> list(CurrentUser user, UUID projectId) {
        projects.requireOwned(user, projectId);
        return jdbc.sql(BASE + " WHERE project_id = :project AND owner_id = :owner ORDER BY created_at DESC")
                .param("project", projectId).param("owner", user.id()).query(SelectionService::map).list();
    }

    private static final String BASE = """
            SELECT id, project_id, source_asset_id, revision, frame_index, timestamp_num, timestamp_den,
                   coordinate_mapping::text AS coordinate_mapping, annotations::text AS annotations,
                   trim_start_seconds, trim_end_seconds, created_at FROM subject_selections
            """;

    private static Selection map(java.sql.ResultSet row, int index) throws java.sql.SQLException {
        return new Selection(row.getObject("id", UUID.class), row.getObject("project_id", UUID.class),
                row.getObject("source_asset_id", UUID.class), row.getInt("revision"), row.getInt("frame_index"),
                row.getLong("timestamp_num"), row.getLong("timestamp_den"), Json.read(row.getString("coordinate_mapping")),
                Json.read(row.getString("annotations")), (Double) row.getObject("trim_start_seconds"),
                (Double) row.getObject("trim_end_seconds"), row.getObject("created_at", OffsetDateTime.class).toInstant());
    }
}
