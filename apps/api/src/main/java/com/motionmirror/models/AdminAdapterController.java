package com.motionmirror.models;

import com.motionmirror.identity.CurrentUserService;
import com.motionmirror.retention.RestoreReconciler;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/** ADMIN only, enforced by the security chain. Requests name adapter ids, never paths or URLs. */
@RestController
@RequestMapping("/api/v1/admin")
public class AdminAdapterController {

    public record RegisterAdapter(@NotNull UUID id, @NotBlank String name, @NotBlank String baseModelId, @NotBlank String baseRevision,
            @NotBlank @Pattern(regexp = "^[a-f0-9]{64}$") String sha256, @NotNull List<String> targetModules, @Positive int rank,
            double scale, @NotBlank String license, @NotBlank String licenseScope, @NotNull JsonNode datasetVersions,
            @NotNull JsonNode trainedOn, JsonNode evalManifest) {}

    public record PresetAction(@NotBlank @Pattern(regexp = "^[a-z0-9-]{1,64}$") String presetId) {}

    public record RevisionView(String presetId, int revision) {}

    public record AdapterList(List<AdapterService.Adapter> items) {}

    private final AdapterService adapters;
    private final CurrentUserService currentUser;
    private final RestoreReconciler reconciler;

    public AdminAdapterController(AdapterService adapters, CurrentUserService currentUser, RestoreReconciler reconciler) {
        this.adapters = adapters;
        this.currentUser = currentUser;
        this.reconciler = reconciler;
    }

    /** Run after restoring a database backup, before letting anyone back in. */
    @PostMapping("/retention/reconcile-after-restore")
    public RestoreReconciler.Result reconcileAfterRestore() {
        return reconciler.reconcile(currentUser.require());
    }

    @PostMapping("/adapters/upload-target")
    public AdapterService.UploadTarget uploadTarget() {
        return adapters.uploadTarget(currentUser.require());
    }

    @PostMapping("/adapters")
    @ResponseStatus(HttpStatus.CREATED)
    public AdapterService.Adapter register(@Valid @RequestBody RegisterAdapter request) {
        return adapters.register(currentUser.require(), new AdapterService.NewAdapter(request.id(), request.name(), request.baseModelId(),
                request.baseRevision(), request.sha256(), request.targetModules(), request.rank(), request.scale(), request.license(),
                request.licenseScope(), request.datasetVersions(), request.trainedOn(), request.evalManifest()));
    }

    @GetMapping("/adapters")
    public AdapterList list() {
        currentUser.require();
        return new AdapterList(adapters.list());
    }

    @PostMapping("/adapters/{id}/evaluation")
    public AdapterService.Adapter evaluation(@PathVariable UUID id, @NotNull @RequestBody JsonNode manifest) {
        return adapters.attachEvaluation(currentUser.require(), id, manifest);
    }

    @PostMapping("/adapters/{id}/stage")
    public RevisionView stage(@PathVariable UUID id, @Valid @RequestBody PresetAction request) {
        return new RevisionView(request.presetId(), adapters.stage(currentUser.require(), id, request.presetId()));
    }

    @PostMapping("/adapters/{id}/promote")
    public RevisionView promote(@PathVariable UUID id, @Valid @RequestBody PresetAction request) {
        return new RevisionView(request.presetId(), adapters.promote(currentUser.require(), id, request.presetId()));
    }

    @PostMapping("/adapters/{id}/deprecate")
    public AdapterService.Adapter deprecate(@PathVariable UUID id) {
        return adapters.deprecate(currentUser.require(), id);
    }

    @PostMapping("/presets/{id}/rollback")
    public RevisionView rollback(@PathVariable String id) {
        return new RevisionView(id, adapters.rollback(currentUser.require(), id));
    }
}
