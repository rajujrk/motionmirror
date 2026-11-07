package com.motionmirror.assets;

import com.motionmirror.identity.CurrentUserService;
import com.motionmirror.shared.Json;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class AssetController {

    public record UploadIntentRequest(@NotBlank String role, @NotBlank @Size(max = 500) String filename,
            @Positive long sizeBytes, @NotBlank String contentType, boolean consentConfirmed) {}

    public record UploadIntentView(UUID assetId, String url, Map<String, String> fields, Instant expiresAt) {}

    public record AssetView(UUID id, UUID projectId, String role, String status, String displayName, Long sizeBytes,
            String detectedType, Object media, String rejectionCode, String rejectionDetail, Instant createdAt) {

        static AssetView of(AssetEntity asset) {
            return new AssetView(asset.getId(), asset.getProjectId(), asset.getRole(), asset.getStatus(),
                    asset.getDisplayName(), asset.getSizeBytes(), asset.getDetectedType(),
                    asset.getMedia() == null ? null : Json.read(asset.getMedia()),
                    asset.getRejectionCode(), asset.getRejectionDetail(), asset.getCreatedAt());
        }
    }

    public record AssetList(List<AssetView> items) {}

    public record DownloadUrl(String url) {}

    private final AssetService assets;
    private final CurrentUserService currentUser;

    public AssetController(AssetService assets, CurrentUserService currentUser) {
        this.assets = assets;
        this.currentUser = currentUser;
    }

    @PostMapping("/projects/{projectId}/assets/upload-intents")
    @ResponseStatus(HttpStatus.CREATED)
    public UploadIntentView createIntent(@PathVariable UUID projectId, @Valid @RequestBody UploadIntentRequest request) {
        AssetService.Intent intent = assets.createIntent(currentUser.require(), projectId, new AssetService.IntentRequest(
                request.role(), request.filename(), request.sizeBytes(), request.contentType(), request.consentConfirmed()));
        return new UploadIntentView(intent.asset().getId(), intent.form().url(), intent.form().fields(), intent.form().expiresAt());
    }

    @PostMapping("/assets/{id}/complete")
    public AssetView complete(@PathVariable UUID id) {
        return AssetView.of(assets.complete(currentUser.require(), id));
    }

    @GetMapping("/assets/{id}")
    public AssetView get(@PathVariable UUID id) {
        return AssetView.of(assets.requireOwned(currentUser.require(), id));
    }

    @GetMapping("/projects/{projectId}/assets")
    public AssetList list(@PathVariable UUID projectId) {
        return new AssetList(assets.list(currentUser.require(), projectId).stream().map(AssetView::of).toList());
    }

    @GetMapping("/assets/{id}/download-url")
    public DownloadUrl downloadUrl(@PathVariable UUID id) {
        return new DownloadUrl(assets.downloadUrl(currentUser.require(), id));
    }
}
