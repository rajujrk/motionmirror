package com.motionmirror.jobs;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.motionmirror.shared.Json;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

class AdapterSnapshotTest {

    private static final ObjectMapper JACKSON_2 = new ObjectMapper();

    private static String written(JsonNode definition) {
        Map<String, Object> inputs = new LinkedHashMap<>();
        inputs.put("adapter", JobSubmissionService.adapterSnapshot(definition));
        return Json.write(inputs);
    }

    @Test
    void theWorkerReceivesTheAdapterFieldsNotASerializedBean() {
        JsonNode definition = JACKSON_2.valueToTree(Map.of("adapter",
                Map.of("id", "adapter-1", "name", "Dance", "sha256", "abc", "scale", 0.8, "license_scope", "research_only")));
        var adapter = Json.read(written(definition)).path("adapter");
        assertThat(adapter.path("id").asString()).isEqualTo("adapter-1");
        assertThat(adapter.path("sha256").asString()).isEqualTo("abc");
        assertThat(adapter.path("scale").asDouble()).isEqualTo(0.8);
        assertThat(adapter.path("license_scope").asString()).isEqualTo("research_only");
        assertThat(adapter.has("nodeType")).as("Jackson 2 node written as a bean").isFalse();
    }

    @Test
    void aPresetWithoutAnAdapterWritesNull() {
        assertThat(Json.read(written(JACKSON_2.valueToTree(Map.of("settings", Map.of())))).path("adapter").isNull()).isTrue();
    }
}
