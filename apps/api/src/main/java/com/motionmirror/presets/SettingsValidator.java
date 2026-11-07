package com.motionmirror.presets;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.motionmirror.shared.ApiException;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.util.Optional;
import java.util.Set;
import org.springframework.stereotype.Component;

/**
 * Validates generation settings against the shared schema, then the rules that would crash the pinned
 * pipeline. The Python worker applies the same rules, and shared fixtures keep the two in step.
 */
@Component
public class SettingsValidator {

    public static final String SCHEMA = "SCHEMA";
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final JsonSchema schema;

    public SettingsValidator() {
        try (InputStream stream = getClass().getResourceAsStream("/contracts/settings/generation-settings.v1.schema.json")) {
            this.schema = JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012).getSchema(stream);
        } catch (IOException error) {
            throw new UncheckedIOException(error);
        }
    }

    /** Returns the first rule the settings break, or empty if they are valid. */
    public Optional<String> firstError(JsonNode settings) {
        Set<ValidationMessage> problems = schema.validate(settings);
        if (!problems.isEmpty()) {
            return Optional.of(SCHEMA);
        }
        if (settings.get("height").asInt() % 16 != 0 || settings.get("width").asInt() % 16 != 0) {
            return Optional.of("SIZE_NOT_DIVISIBLE_BY_16");
        }
        int segment = settings.get("segment_frame_length").asInt();
        int conditioning = settings.get("prev_segment_conditioning_frames").asInt();
        if (segment % 4 != 1) {
            return Optional.of("SEGMENT_NOT_4N_PLUS_1");
        }
        if (conditioning % 4 != 1) {
            return Optional.of("CONDITIONING_NOT_4N_PLUS_1");
        }
        if (segment < 2 * conditioning) {
            return Optional.of("SEGMENT_TOO_SHORT_FOR_CONDITIONING");
        }
        boolean hasNegative = settings.hasNonNull("negative_prompt") && !settings.get("negative_prompt").asText().isBlank();
        if (hasNegative && settings.get("guidance_scale").asDouble() <= 1.0) {
            return Optional.of("NEGATIVE_PROMPT_REQUIRES_GUIDANCE");
        }
        return Optional.empty();
    }

    public void requireValid(JsonNode settings) {
        firstError(settings).ifPresent(code -> {
            throw ApiException.badRequest(code, describe(code));
        });
    }

    public static JsonNode parse(String json) {
        try {
            return MAPPER.readTree(json);
        } catch (IOException error) {
            throw new UncheckedIOException(error);
        }
    }

    private static String describe(String code) {
        return switch (code) {
            case "SIZE_NOT_DIVISIBLE_BY_16" -> "Height and width must be divisible by 16";
            case "SEGMENT_NOT_4N_PLUS_1" -> "Segment length must be 4N+1 frames, for example 77";
            case "CONDITIONING_NOT_4N_PLUS_1" -> "Conditioning frames must be 4N+1, for example 1 or 5";
            case "SEGMENT_TOO_SHORT_FOR_CONDITIONING" -> "Segment length must be at least twice the conditioning frames";
            case "NEGATIVE_PROMPT_REQUIRES_GUIDANCE" ->
                    "A negative prompt only has an effect with guidance above 1. Raise guidance or remove it";
            default -> "These settings aren't supported";
        };
    }
}
