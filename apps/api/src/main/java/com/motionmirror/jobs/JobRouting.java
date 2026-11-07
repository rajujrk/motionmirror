package com.motionmirror.jobs;

import com.motionmirror.config.AppProperties;
import org.springframework.stereotype.Component;

/** Trusted deployment settings choose the backend. A public request can never name one. */
@Component
public class JobRouting {

    public static final String MOCK = "mock";
    public static final String WAN = "wan-animate";
    public static final String REAL_PREPROCESS = "real-preprocess";
    public static final String MEDIA_VALIDATE = "media-validate";

    public record Route(String backendId, String routingKey) {}

    private final AppProperties.Backends backends;

    public JobRouting(AppProperties properties) {
        this.backends = properties.backends();
    }

    public Route routeFor(JobKind kind) {
        // Checking an upload needs no GPU, so it must never keep a paid GPU worker busy or running.
        if (kind == JobKind.VALIDATE) {
            return new Route(MEDIA_VALIDATE, "validate");
        }
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
