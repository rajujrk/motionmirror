package com.motionmirror.security;

import static org.assertj.core.api.Assertions.assertThat;

import com.motionmirror.config.AppProperties;
import com.motionmirror.config.StartupChecks;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class StartupChecksTest {

    private static final long SIX_HOURS = Duration.ofHours(6).toMillis();

    private static AppProperties properties(String deployment, String backend, String scheme) {
        return new AppProperties(deployment, scheme + "://app.test",
                new AppProperties.Backends(backend, backend),
                new AppProperties.Oidc(scheme + "://id.test", "http://kc", "web", "secret", "aud", Map.of()),
                new AppProperties.Storage("http://s3", scheme + "://s3.test", "http://s3", "a", "b", "bucket", "us-east-1",
                        Duration.ofMinutes(15), Duration.ofMinutes(5), Duration.ofMinutes(30)),
                new AppProperties.Limits(1, 30, 1, 2, 20, 60),
                new AppProperties.Jobs(Duration.ofMinutes(2), 3, Duration.ofSeconds(30), Duration.ofHours(4), Duration.ofHours(8),
                        Duration.ofMillis(250), Duration.ofSeconds(30), Duration.ofSeconds(10), Duration.ofSeconds(15)));
    }

    @Test
    void localMockModeIsAllowed() {
        assertThat(StartupChecks.problems(properties("local", "mock", "http"), false, SIX_HOURS)).isEmpty();
    }

    @Test
    void productionRefusesMockMode() {
        assertThat(StartupChecks.problems(properties("production", "mock", "https"), true, SIX_HOURS))
                .anyMatch(problem -> problem.contains("mock generation"));
    }

    @Test
    void productionRefusesInsecureDefaults() {
        List<String> problems = StartupChecks.problems(properties("production", "wan-animate", "http"), false, SIX_HOURS);
        assertThat(problems).anyMatch(problem -> problem.contains("Secure"));
        assertThat(problems).anyMatch(problem -> problem.contains("public origin"));
        assertThat(problems).anyMatch(problem -> problem.contains("OIDC issuer"));
        assertThat(problems).anyMatch(problem -> problem.contains("storage endpoint"));
    }

    @Test
    void secureProductionPasses() {
        assertThat(StartupChecks.problems(properties("production", "wan-animate", "https"), true, SIX_HOURS)).isEmpty();
    }

    @Test
    void shortBrokerDeadlineIsRefused() {
        assertThat(StartupChecks.problems(properties("local", "mock", "http"), false, Duration.ofMinutes(30).toMillis()))
                .anyMatch(problem -> problem.contains("consumer timeout"));
    }
}
