package com.motionmirror.presets;

import com.fasterxml.jackson.databind.JsonNode;
import com.motionmirror.cache.MetadataCache;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.identity.CurrentUserService;
import java.time.Duration;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/presets")
public class PresetController {

    public record PresetView(String id, int revision, String displayName, String configRevision, String settingsJson) {}

    public record PresetList(List<PresetView> items) {}

    private final PresetService presets;
    private final CurrentUserService currentUser;
    private final MetadataCache cache;

    public PresetController(PresetService presets, CurrentUserService currentUser, MetadataCache cache) {
        this.presets = presets;
        this.currentUser = currentUser;
        this.cache = cache;
    }

    @GetMapping
    public PresetList list() {
        CurrentUser user = currentUser.require();
        String scope = user.isAdmin() ? "admin" : "public";
        return cache.getOrLoad("presets:" + scope, Duration.ofMinutes(5), PresetList.class, () -> new PresetList(
                presets.listCurrent(user.isAdmin()).stream()
                        .map(preset -> new PresetView(preset.id(), preset.revision(), preset.displayName(),
                                preset.configRevision(), String.valueOf((JsonNode) preset.settings())))
                        .toList()));
    }
}
