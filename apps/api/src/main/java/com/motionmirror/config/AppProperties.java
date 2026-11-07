package com.motionmirror.config;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties("motionmirror")
public record AppProperties(
        String deployment,
        String publicOrigin,
        Backends backends,
        Oidc oidc,
        Storage storage,
        Limits limits,
        Jobs jobs) {

    public boolean isProduction() {
        return "production".equals(deployment);
    }

    public record Backends(String generate, String preprocess) {}

    public record Oidc(
            String publicIssuer,
            String internalBase,
            String clientId,
            String clientSecret,
            String workerAudience,
            Map<String, List<String>> workerBackends) {}

    public record Storage(
            String internalEndpoint,
            String publicEndpoint,
            String workerEndpoint,
            String accessKey,
            String secretKey,
            String bucket,
            String region,
            Duration uploadUrlTtl,
            Duration downloadUrlTtl,
            Duration workerUrlTtl) {}

    public record Limits(
            long maxSourceBytes,
            int maxSourceSeconds,
            long maxReferenceBytes,
            int maxActiveJobsPerUser,
            int submissionsPerMinute,
            int uploadIntentsPerMinute) {}

    public record Jobs(
            Duration leaseTtl,
            int maxAttempts,
            Duration retryBaseDelay,
            Duration maxAttemptDuration,
            Duration queuedRedispatchAfter,
            Duration outboxPoll,
            Duration outboxReservation,
            Duration publishConfirmTimeout,
            Duration reconcileInterval) {}
}
