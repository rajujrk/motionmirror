package com.motionmirror.shared;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** Plain JSON helpers for JSONB columns and message payloads. */
public final class Json {

    private static final JsonMapper MAPPER = JsonMapper.builder().build();

    private Json() {}

    public static String write(Object value) {
        return MAPPER.writeValueAsString(value);
    }

    public static JsonNode read(String json) {
        return MAPPER.readTree(json);
    }

    public static <T> T read(String json, Class<T> type) {
        return MAPPER.readValue(json, type);
    }

    public static JsonNode tree(Object value) {
        return MAPPER.valueToTree(value);
    }
}
