package com.motionmirror.contracts;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.motionmirror.presets.SettingsValidator;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;

/** Python runs these same fixtures, so both languages accept and reject the same inputs. */
class SharedFixturesTest {

    private static final Path FIXTURES = Path.of("../../packages/contracts/fixtures").toAbsolutePath().normalize();
    private static final ObjectMapper MAPPER = new ObjectMapper();

    static Stream<Path> validSettings() throws IOException {
        return list("generation-settings/valid");
    }

    static Stream<Path> invalidSettings() throws IOException {
        return list("generation-settings/invalid");
    }

    static Stream<Path> validEnvelopes() throws IOException {
        return list("task-envelope/valid");
    }

    static Stream<Path> invalidEnvelopes() throws IOException {
        return list("task-envelope/invalid");
    }

    private static Stream<Path> list(String directory) throws IOException {
        try (Stream<Path> files = Files.list(FIXTURES.resolve(directory))) {
            List<Path> paths = files.sorted().toList();
            assertThat(paths).isNotEmpty();
            return paths.stream();
        }
    }

    private final SettingsValidator validator = new SettingsValidator();

    @ParameterizedTest
    @MethodSource("validSettings")
    void acceptsValidSettings(Path file) throws IOException {
        assertThat(validator.firstError(MAPPER.readTree(file.toFile()))).isEmpty();
    }

    @ParameterizedTest
    @MethodSource("invalidSettings")
    void rejectsInvalidSettingsWithTheSharedCode(Path file) throws IOException {
        JsonNode fixture = MAPPER.readTree(file.toFile());
        assertThat(validator.firstError(fixture.get("settings"))).contains(fixture.get("expected_error").asText());
    }

    @ParameterizedTest
    @MethodSource("validEnvelopes")
    void acceptsValidEnvelopes(Path file) throws IOException {
        assertThat(envelopeSchema().validate(MAPPER.readTree(file.toFile()))).isEmpty();
    }

    @ParameterizedTest
    @MethodSource("invalidEnvelopes")
    void rejectsInvalidEnvelopes(Path file) throws IOException {
        assertThat(envelopeSchema().validate(MAPPER.readTree(file.toFile()))).isNotEmpty();
    }

    private static JsonSchema envelopeSchema() throws IOException {
        try (InputStream stream = SharedFixturesTest.class.getResourceAsStream("/contracts/messages/task-envelope.v1.schema.json")) {
            return JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012).getSchema(stream);
        }
    }
}
