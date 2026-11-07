package com.motionmirror.selections;

import com.motionmirror.identity.CurrentUserService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;
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

@RestController
@RequestMapping("/api/v1/projects/{projectId}")
public class SelectionController {

    public record CreateSelection(@NotNull UUID sourceAssetId, @PositiveOrZero int frameIndex, @PositiveOrZero long timestampNum,
            @Positive long timestampDen, @NotNull JsonNode coordinateMapping, @NotNull JsonNode annotations,
            Double trimStartSeconds, Double trimEndSeconds) {}

    public record SelectionList(List<SelectionService.Selection> items) {}

    public record ReferenceRequest(@NotNull UUID assetId, @NotBlank String viewLabel, boolean primary) {}

    public record CreateProfile(@NotBlank @Size(max = 200) String name, @NotNull @Size(min = 1, max = 12) List<@Valid ReferenceRequest> references) {}

    public record ProfileList(List<AppearanceService.Profile> items) {}

    private final SelectionService selections;
    private final AppearanceService appearance;
    private final CurrentUserService currentUser;

    public SelectionController(SelectionService selections, AppearanceService appearance, CurrentUserService currentUser) {
        this.selections = selections;
        this.appearance = appearance;
        this.currentUser = currentUser;
    }

    @PostMapping("/subject-selections")
    @ResponseStatus(HttpStatus.CREATED)
    public SelectionService.Selection createSelection(@PathVariable UUID projectId, @Valid @RequestBody CreateSelection request) {
        return selections.create(currentUser.require(), projectId, new SelectionService.NewSelection(
                request.sourceAssetId(), request.frameIndex(), request.timestampNum(), request.timestampDen(),
                request.coordinateMapping(), request.annotations(), request.trimStartSeconds(), request.trimEndSeconds()));
    }

    @GetMapping("/subject-selections")
    public SelectionList listSelections(@PathVariable UUID projectId) {
        return new SelectionList(selections.list(currentUser.require(), projectId));
    }

    @PostMapping("/appearance-profiles")
    @ResponseStatus(HttpStatus.CREATED)
    public AppearanceService.Profile createProfile(@PathVariable UUID projectId, @Valid @RequestBody CreateProfile request) {
        return appearance.create(currentUser.require(), projectId, request.name().strip(), request.references().stream()
                .map(reference -> new AppearanceService.ReferenceInput(reference.assetId(), reference.viewLabel(), reference.primary()))
                .toList());
    }

    @GetMapping("/appearance-profiles")
    public ProfileList listProfiles(@PathVariable UUID projectId) {
        return new ProfileList(appearance.list(currentUser.require(), projectId));
    }
}
