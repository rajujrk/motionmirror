package com.motionmirror.assets;

import com.motionmirror.audit.AuditService;
import com.motionmirror.cache.RateLimiter;
import com.motionmirror.config.AppProperties;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.jobs.JobKind;
import com.motionmirror.jobs.JobService;
import com.motionmirror.projects.ProjectService;
import com.motionmirror.shared.ApiException;
import com.motionmirror.shared.Json;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Upload lifecycle. Bytes go to a per-upload key, then move to a server-only key the moment the upload is
 * complete. A still-valid upload form can't change anything after that, so validation can't be raced.
 */
@Service
public class AssetService {

    private static final Set<String> VIDEO_TYPES = Set.of("video/mp4", "video/quicktime");
    private static final Set<String> IMAGE_TYPES = Set.of("image/jpeg", "image/png", "image/webp");

    public record IntentRequest(String role, String filename, long sizeBytes, String contentType, boolean consentConfirmed) {}

    public record Intent(AssetEntity asset, ObjectStorage.UploadForm form) {}

    private final AssetRepository assets;
    private final ProjectService projects;
    private final ObjectStorage storage;
    private final JobService jobs;
    private final RateLimiter limiter;
    private final AuditService audit;
    private final AppProperties properties;

    public AssetService(AssetRepository assets, ProjectService projects, ObjectStorage storage, JobService jobs,
            RateLimiter limiter, AuditService audit, AppProperties properties) {
        this.assets = assets;
        this.projects = projects;
        this.storage = storage;
        this.jobs = jobs;
        this.limiter = limiter;
        this.audit = audit;
        this.properties = properties;
    }

    @Transactional
    public Intent createIntent(CurrentUser user, UUID projectId, IntentRequest request) {
        projects.requireOwned(user, projectId);
        if (!request.consentConfirmed()) {
            throw ApiException.badRequest("CONSENT_REQUIRED", "Confirm you have permission to use this media");
        }
        long maxBytes = validateType(request);
        if (request.sizeBytes() > maxBytes) {
            throw ApiException.badRequest("FILE_TOO_LARGE", "This file is larger than the " + maxBytes / (1024 * 1024) + " MB limit");
        }
        limiter.check("upload-intent", user.id(), properties.limits().uploadIntentsPerMinute());

        UUID assetId = UUID.randomUUID();
        String uploadKey = "uploads/" + user.id() + "/" + projectId + "/" + assetId + "/upload";
        ObjectStorage.UploadForm form = storage.presignUploadForm(uploadKey, request.contentType(), maxBytes);
        AssetEntity asset = assets.save(new AssetEntity(assetId, projectId, user.id(), request.role(),
                displayName(request.filename()), request.contentType(), request.sizeBytes(), uploadKey, form.expiresAt(), true));
        audit.user(user, "ASSET_UPLOAD_INTENT", "asset", assetId.toString(), Map.of("role", request.role()));
        return new Intent(asset, form);
    }

    private long validateType(IntentRequest request) {
        if (AssetEntity.DRIVING_VIDEO.equals(request.role())) {
            requireType(VIDEO_TYPES, request.contentType(), "Upload an MP4 or MOV video");
            return properties.limits().maxSourceBytes();
        }
        if (AssetEntity.TARGET_REFERENCE.equals(request.role())) {
            requireType(IMAGE_TYPES, request.contentType(), "Upload a JPEG, PNG or WebP photo");
            return properties.limits().maxReferenceBytes();
        }
        throw ApiException.badRequest("UNKNOWN_ROLE", "Unknown asset role");
    }

    private static void requireType(Set<String> allowed, String type, String message) {
        if (!allowed.contains(type)) {
            throw ApiException.badRequest("UNSUPPORTED_MEDIA_TYPE", message);
        }
    }

    /** The name is display metadata only. It never reaches a storage key, a path or a shell. */
    static String displayName(String filename) {
        String name = filename == null ? "" : filename.replaceAll(".*[/\\\\]", "").replaceAll("[\\p{Cntrl}\"<>]", "").strip();
        if (name.isEmpty()) {
            return "untitled";
        }
        return name.length() > 200 ? name.substring(0, 200) : name;
    }

    @Transactional
    public AssetEntity complete(CurrentUser user, UUID assetId) {
        AssetEntity asset = requireOwned(user, assetId);
        if (!AssetEntity.PENDING_UPLOAD.equals(asset.getStatus())) {
            return asset;
        }
        if (asset.getUploadExpiresAt().isBefore(Instant.now().minus(properties.storage().uploadUrlTtl()))) {
            throw ApiException.conflict("UPLOAD_EXPIRED", "This upload expired. Start it again");
        }
        ObjectStorage.ObjectInfo uploaded = storage.head(asset.getUploadKey())
                .orElseThrow(() -> ApiException.conflict("UPLOAD_NOT_FOUND", "The upload hasn't arrived yet"));
        if (uploaded.sizeBytes() != asset.getDeclaredBytes()) {
            asset.reject("SIZE_MISMATCH", "The uploaded file isn't the size that was declared");
            storage.delete(asset.getUploadKey());
            return assets.save(asset);
        }
        String immutableKey = "assets/" + asset.getOwnerId() + "/" + asset.getProjectId() + "/" + asset.getId() + "/original";
        storage.copy(asset.getUploadKey(), immutableKey);
        storage.delete(asset.getUploadKey());
        asset.quarantine(immutableKey, uploaded.sizeBytes());
        assets.save(asset);

        // Bounded checks run in the worker, isolated from the API. The asset stays unusable until they pass.
        Map<String, Object> inputs = Map.of(
                "asset_id", asset.getId().toString(),
                "role", asset.getRole(),
                "declared_type", asset.getDeclaredType(),
                "max_seconds", properties.limits().maxSourceSeconds());
        jobs.submit(asset.getOwnerId(), new JobService.Submission(asset.getProjectId(), JobKind.VALIDATE, "validate@1",
                "{}", "{}", Json.write(inputs), "validate:" + asset.getId(), true));
        audit.user(user, "ASSET_UPLOADED", "asset", assetId.toString(), Map.of("bytes", uploaded.sizeBytes()));
        return asset;
    }

    @Transactional(readOnly = true)
    public AssetEntity requireOwned(CurrentUser user, UUID assetId) {
        AssetEntity asset = assets.findByIdAndOwnerId(assetId, user.id()).orElseThrow(() -> ApiException.notFound("Asset"));
        if (AssetEntity.DELETED.equals(asset.getStatus())) {
            throw ApiException.notFound("Asset");
        }
        return asset;
    }

    /** Nested references must be yours, in this project, of this role and validated. */
    @Transactional(readOnly = true)
    public AssetEntity requireUsable(CurrentUser user, UUID projectId, UUID assetId, String role) {
        AssetEntity asset = requireOwned(user, assetId);
        if (!asset.getProjectId().equals(projectId)) {
            throw ApiException.notFound("Asset");
        }
        if (!role.equals(asset.getRole())) {
            throw ApiException.badRequest("WRONG_ASSET_ROLE", "This file can't be used here");
        }
        if (!AssetEntity.READY.equals(asset.getStatus())) {
            throw ApiException.conflict("ASSET_NOT_READY", "This file is still being checked or was rejected");
        }
        return asset;
    }

    @Transactional(readOnly = true)
    public List<AssetEntity> list(CurrentUser user, UUID projectId) {
        projects.requireOwned(user, projectId);
        return assets.findByProjectIdAndStatusNotOrderByCreatedAtDesc(projectId, AssetEntity.DELETED);
    }

    @Transactional(readOnly = true)
    public String downloadUrl(CurrentUser user, UUID assetId) {
        AssetEntity asset = requireOwned(user, assetId);
        if (!AssetEntity.READY.equals(asset.getStatus())) {
            throw new ApiException(HttpStatus.CONFLICT, "ASSET_NOT_READY", "This file isn't available");
        }
        return storage.presignBrowserDownload(asset.getObjectKey(), asset.getId() + extensionFor(asset));
    }

    private static String extensionFor(AssetEntity asset) {
        return switch (String.valueOf(asset.getDeclaredType())) {
            case "video/mp4" -> ".mp4";
            case "video/quicktime" -> ".mov";
            case "image/jpeg" -> ".jpg";
            case "image/png" -> ".png";
            case "image/webp" -> ".webp";
            default -> "";
        };
    }
}
