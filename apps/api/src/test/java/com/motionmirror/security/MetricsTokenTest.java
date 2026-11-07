package com.motionmirror.security;

import static org.assertj.core.api.Assertions.assertThat;

import com.motionmirror.config.SecurityConfig;
import org.junit.jupiter.api.Test;

class MetricsTokenTest {

    @Test
    void theRightTokenMatches() {
        assertThat(SecurityConfig.metricsTokenMatches("secret", "Bearer secret")).isTrue();
    }

    @Test
    void aWrongOrMissingHeaderDoesNot() {
        assertThat(SecurityConfig.metricsTokenMatches("secret", "Bearer other")).isFalse();
        assertThat(SecurityConfig.metricsTokenMatches("secret", "secret")).isFalse();
        assertThat(SecurityConfig.metricsTokenMatches("secret", null)).isFalse();
    }

    @Test
    void withNoTokenConfiguredMetricsStayClosedEvenToAnEmptyBearer() {
        assertThat(SecurityConfig.metricsTokenMatches("", "Bearer ")).isFalse();
        assertThat(SecurityConfig.metricsTokenMatches(null, "Bearer anything")).isFalse();
        assertThat(SecurityConfig.metricsTokenMatches("  ", "Bearer   ")).isFalse();
    }
}
