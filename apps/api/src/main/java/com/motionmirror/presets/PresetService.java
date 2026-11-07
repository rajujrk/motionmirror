package com.motionmirror.presets;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.motionmirror.shared.ApiException;
import jakarta.annotation.PostConstruct;
import java.io.IOException;
import java.io.InputStream;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.PathMatchingResourcePatternResolver;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.yaml.snakeyaml.Yaml;

/** Immutable, versioned presets. Jobs snapshot a revision, so promoting a preset never changes running work. */
@Service
public class PresetService {

    public record Preset(String id, int revision, String displayName, String backendId, JsonNode definition,
            String visibility, boolean isDefaultRevision) {

        public String configRevision() {
            return id + "@" + revision;
        }

        public JsonNode settings() {
            return definition.get("settings");
        }
    }

    private static final Logger log = LoggerFactory.getLogger(PresetService.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final JdbcClient jdbc;
    private final SettingsValidator validator;

    public PresetService(JdbcClient jdbc, SettingsValidator validator) {
        this.jdbc = jdbc;
        this.validator = validator;
    }

    /** Loads checked-in presets. Existing revisions are left alone because revisions are immutable. */
    @PostConstruct
    @Transactional
    public void seed() {
        try {
            for (Resource resource : new PathMatchingResourcePatternResolver().getResources("classpath:/presets/*.yaml")) {
                seedOne(resource);
            }
        } catch (IOException error) {
            throw new IllegalStateException("cannot read presets", error);
        }
    }

    private void seedOne(Resource resource) throws IOException {
        try (InputStream stream = resource.getInputStream()) {
            Map<String, Object> parsed = new Yaml().load(stream);
            JsonNode definition = MAPPER.valueToTree(parsed);
            validator.requireValid(definition.get("settings"));
            seedDefinition(definition);
        }
    }

    /**
     * Revisions are immutable and running jobs point at them, so a file that changes an existing revision stops
     * startup rather than being ignored. A new revision becomes the default unless an adapter currently holds that
     * place: dropping an adapter should be a decision, not a side effect of a deploy.
     */
    public void seedDefinition(JsonNode definition) {
        String id = definition.get("id").asText();
        int revision = definition.get("revision").asInt();
        int inserted = jdbc.sql("""
                INSERT INTO presets (id, revision, display_name, backend_id, definition)
                VALUES (:id, :revision, :name, 'configured', CAST(:definition AS jsonb))
                ON CONFLICT DO NOTHING
                """)
                .param("id", id)
                .param("revision", revision)
                .param("name", definition.get("display_name").asText())
                .param("definition", definition.toString())
                .update();
        if (inserted == 0) {
            boolean same = jdbc.sql("SELECT definition = CAST(:definition AS jsonb) FROM presets WHERE id = :id AND revision = :revision")
                    .param("definition", definition.toString()).param("id", id).param("revision", revision)
                    .query(Boolean.class).single();
            if (!same) {
                throw new IllegalStateException("preset " + id + "@" + revision + " differs from what is stored. Give the file a new revision");
            }
            return;
        }
        log.info("seeded preset {}@{}", id, revision);
        int moved = jdbc.sql("""
                INSERT INTO preset_heads (id, current_revision) VALUES (:id, :revision)
                ON CONFLICT (id) DO UPDATE SET current_revision = EXCLUDED.current_revision, updated_at = now()
                WHERE preset_heads.current_revision < EXCLUDED.current_revision
                  AND NOT EXISTS (SELECT 1 FROM presets p WHERE p.id = preset_heads.id
                      AND p.revision = preset_heads.current_revision AND p.adapter_id IS NOT NULL)
                """).param("id", id).param("revision", revision).update();
        if (moved == 0) {
            log.warn("preset {}@{} was added but isn't the default. Check the current default", id, revision);
        }
    }

    public List<Preset> listCurrent(boolean includeAdminOnly) {
        return jdbc.sql("""
                SELECT p.id, p.revision, p.display_name, p.backend_id, p.definition::text AS definition, p.visibility, TRUE AS is_default
                FROM presets p JOIN preset_heads h ON h.id = p.id AND h.current_revision = p.revision
                WHERE p.visibility = 'PUBLIC' OR :admin
                ORDER BY p.id
                """)
                .param("admin", includeAdminOnly)
                .query((row, index) -> new Preset(row.getString("id"), row.getInt("revision"),
                        row.getString("display_name"), row.getString("backend_id"),
                        SettingsValidator.parse(row.getString("definition")), row.getString("visibility"), true))
                .list();
    }

    public Preset requireCurrent(String id, boolean admin) {
        return listCurrent(admin).stream()
                .filter(preset -> preset.id().equals(id))
                .findFirst()
                .orElseThrow(() -> ApiException.badRequest("UNKNOWN_PRESET", "Unknown preset"));
    }

    /** Accepts "standard" for the current default or "standard@3" for an exact revision. Staged ones need ADMIN. */
    public Preset resolveReference(String reference, boolean admin) {
        int separator = reference.indexOf('@');
        if (separator < 0) {
            return requireCurrent(reference, admin);
        }
        String id = reference.substring(0, separator);
        int revision;
        try {
            revision = Integer.parseInt(reference.substring(separator + 1));
        } catch (NumberFormatException invalid) {
            throw ApiException.badRequest("UNKNOWN_PRESET", "Unknown preset");
        }
        return jdbc.sql("""
                SELECT p.id, p.revision, p.display_name, p.backend_id, p.definition::text AS definition, p.visibility,
                       (h.current_revision = p.revision) AS is_default
                FROM presets p JOIN preset_heads h ON h.id = p.id
                WHERE p.id = :id AND p.revision = :revision AND (p.visibility = 'PUBLIC' OR :admin)
                """)
                .param("id", id).param("revision", revision).param("admin", admin)
                .query((row, index) -> new Preset(row.getString("id"), row.getInt("revision"), row.getString("display_name"),
                        row.getString("backend_id"), SettingsValidator.parse(row.getString("definition")),
                        row.getString("visibility"), row.getBoolean("is_default")))
                .optional()
                .orElseThrow(() -> ApiException.badRequest("UNKNOWN_PRESET", "Unknown preset"));
    }

    /** Overlays user choices on the preset, swaps to the source's orientation, then validates the result. */
    public ObjectNode resolve(Preset preset, JsonNode overrides, boolean portraitSource) {
        ObjectNode settings = preset.settings().deepCopy();
        if (overrides != null && overrides.isObject()) {
            settings.setAll((ObjectNode) overrides);
        }
        int height = settings.path("height").asInt();
        int width = settings.path("width").asInt();
        if (portraitSource != height > width && height != width) {
            settings.put("height", width);
            settings.put("width", height);
        }
        validator.requireValid(settings);
        return settings;
    }
}
