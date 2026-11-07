package com.motionmirror.jobs;

import static org.assertj.core.api.Assertions.assertThat;

import com.motionmirror.shared.Json;
import java.util.Map;
import org.junit.jupiter.api.Test;

class OutputLabelTest {

    @Test
    void mockIsAlwaysMock() {
        assertThat(JobController.outputLabel("mock", Json.tree(Map.of("device_type", "cuda")))).isEqualTo("mock");
    }

    @Test
    void onlyARunRecordedOnCudaIsReal() {
        assertThat(JobController.outputLabel("wan-animate", Json.tree(Map.of("device_type", "cuda")))).isEqualTo("real");
    }

    @Test
    void aMacOrCpuRunIsExperimental() {
        assertThat(JobController.outputLabel("wan-animate", Json.tree(Map.of("device_type", "mps")))).isEqualTo("experimental");
        assertThat(JobController.outputLabel("real-preprocess", Json.tree(Map.of("device_type", "cpu")))).isEqualTo("experimental");
    }

    @Test
    void aJobThatHasntRunHasntEarnedTheRealLabel() {
        assertThat(JobController.outputLabel("wan-animate", Json.tree(Map.of()))).isEqualTo("experimental");
    }
}
