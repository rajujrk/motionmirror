package com.motionmirror.config;

import java.net.URI;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import jakarta.annotation.PostConstruct;

/** Refuses unsafe deployment settings at startup rather than discovering them in production. */
@Configuration
public class StartupChecks {

    private static final Duration CLEANUP_MARGIN = Duration.ofMinutes(30);

    private final AppProperties properties;
    private final boolean secureCookie;
    private final long consumerTimeoutMillis;
    private final boolean rabbitTls;
    private final boolean redisTls;

    public StartupChecks(AppProperties properties,
            @Value("${server.servlet.session.cookie.secure}") boolean secureCookie,
            @Value("${motionmirror.jobs.broker-consumer-timeout-ms:21600000}") long consumerTimeoutMillis,
            @Value("${spring.rabbitmq.ssl.enabled:false}") boolean rabbitTls,
            @Value("${spring.data.redis.ssl.enabled:false}") boolean redisTls) {
        this.properties = properties;
        this.secureCookie = secureCookie;
        this.consumerTimeoutMillis = consumerTimeoutMillis;
        this.rabbitTls = rabbitTls;
        this.redisTls = redisTls;
    }

    @PostConstruct
    void check() {
        List<String> problems = problems(properties, secureCookie, consumerTimeoutMillis, rabbitTls, redisTls);
        if (!problems.isEmpty()) {
            throw new IllegalStateException("Unsafe configuration: " + String.join("; ", problems));
        }
    }

    /** Separate from the bean so each rule can be tested directly. */
    public static List<String> problems(AppProperties properties, boolean secureCookie, long consumerTimeoutMillis,
            boolean rabbitTls, boolean redisTls) {
        List<String> problems = new ArrayList<>();
        Duration needed = properties.jobs().maxAttemptDuration().plus(CLEANUP_MARGIN);
        if (consumerTimeoutMillis < needed.toMillis()) {
            problems.add("broker consumer timeout must be at least " + needed + " so healthy long attempts aren't redelivered");
        }
        if (properties.isProduction()) {
            if ("mock".equals(properties.backends().generate())) {
                problems.add("mock generation is not allowed in production");
            }
            if ("mock".equals(properties.backends().preprocess())) {
                problems.add("mock preprocessing is not allowed in production");
            }
            if (!secureCookie) {
                problems.add("session cookies must be Secure in production");
            }
            if (!isHttps(properties.publicOrigin())) {
                problems.add("the public origin must use HTTPS in production");
            }
            if (!isHttps(properties.oidc().publicIssuer())) {
                problems.add("the OIDC issuer must use HTTPS in production");
            }
            if (!isHttps(properties.storage().publicEndpoint())) {
                problems.add("the storage endpoint must use HTTPS in production");
            }
            if (!rabbitTls) {
                problems.add("the connection to RabbitMQ must use TLS in production");
            }
            if (!redisTls) {
                problems.add("the connection to Redis must use TLS in production");
            }
        }
        return problems;
    }

    private static boolean isHttps(String url) {
        return "https".equals(URI.create(url).getScheme());
    }
}
