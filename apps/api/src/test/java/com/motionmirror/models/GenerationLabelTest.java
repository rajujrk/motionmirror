package com.motionmirror.models;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;

class GenerationLabelTest {

    private static CapabilitiesController.WorkerView worker(String backends, String device, boolean online) {
        return new CapabilitiesController.WorkerView("w", backends, device, "{}", Instant.now(), online);
    }

    @Test
    void mockConfigurationIsMock() {
        assertThat(CapabilitiesController.generationLabel("mock", List.of())).isEqualTo("mock");
    }

    @Test
    void anOnlineCudaWorkerMakesItReal() {
        assertThat(CapabilitiesController.generationLabel("wan-animate", List.of(worker("[\"wan-animate\"]", "cuda", true)))).isEqualTo("real");
    }

    @Test
    void configuringARealBackendIsNotEnoughOnItsOwn() {
        assertThat(CapabilitiesController.generationLabel("wan-animate", List.of())).isEqualTo("experimental");
        assertThat(CapabilitiesController.generationLabel("wan-animate", List.of(worker("[\"wan-animate\"]", "cuda", false)))).isEqualTo("experimental");
    }

    @Test
    void aMacWorkerIsExperimental() {
        assertThat(CapabilitiesController.generationLabel("wan-animate", List.of(worker("[\"wan-animate\"]", "mps", true)))).isEqualTo("experimental");
    }

    @Test
    void aCudaWorkerForAnotherBackendDoesntCount() {
        assertThat(CapabilitiesController.generationLabel("wan-animate", List.of(worker("[\"real-preprocess\"]", "cuda", true)))).isEqualTo("experimental");
    }
}
