package com.motionmirror.jobs;

import com.motionmirror.config.AppProperties;
import org.springframework.stereotype.Component;

/** Trusted deployment settings choose the backend. A public request can never name one. */
@Component
public class JobRouting {

    public static final String MOCK = "mock";
    public static final String WAN = "wan-animate";
    public static final String REAL_PREPROCESS = "real-preprocess";

    public record Route(String backendId, String routingKey) {}

    private final AppProperties.Backends backends;

    public JobRouting(AppProperties properties) {
        this.backends = properties.backends();
    }

    public Route routeFor(JobKind kind) {
        if (kind.isGeneration()) {
            return MOCK.equals(backends.generate())
                    ? new Route(MOCK, "generate.mock")
                    : new Route(WAN, "generate.wan-animate");
        }
        return MOCK.equals(backends.preprocess())
                ? new Route(MOCK, "preprocess.mock")
                : new Route(REAL_PREPROCESS, "preprocess.real");
    }
}
