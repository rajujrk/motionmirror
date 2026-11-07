package com.motionmirror.models;

import com.motionmirror.cache.MetadataCache;
import com.motionmirror.config.AppProperties;
import com.motionmirror.identity.CurrentUserService;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Static capabilities are separate from which workers are actually online. */
@RestController
@RequestMapping("/api/v1/models")
public class CapabilitiesController {

    public record WorkerView(String workerId, String backends, String deviceType, String capabilities, Instant lastSeen, boolean online) {}

    /** mock, real or experimental. Only CUDA workers can make generation real. */
    public record Capabilities(String generateBackend, String preprocessBackend, String generationLabel, int retentionDays, List<WorkerView> workers) {}

    private static final Duration ONLINE_WINDOW = Duration.ofMinutes(2);

    private final JdbcClient jdbc;
    private final AppProperties properties;
    private final MetadataCache cache;
    private final CurrentUserService currentUser;

    public CapabilitiesController(JdbcClient jdbc, AppProperties properties, MetadataCache cache, CurrentUserService currentUser) {
        this.jdbc = jdbc;
        this.properties = properties;
        this.cache = cache;
        this.currentUser = currentUser;
    }

    @GetMapping("/capabilities")
    public Capabilities capabilities() {
        currentUser.require();
        return cache.getOrLoad("capabilities", Duration.ofSeconds(20), Capabilities.class, this::load);
    }

    /** What a job would produce now. With no CUDA worker online the honest answer isn't real, whatever is configured. */
    static String generationLabel(String generateBackend, List<WorkerView> workers) {
        if ("mock".equals(generateBackend)) {
            return "mock";
        }
        boolean cudaOnline = workers.stream()
                .anyMatch(worker -> worker.online() && "cuda".equals(worker.deviceType()) && worker.backends().contains(generateBackend));
        return cudaOnline ? "real" : "experimental";
    }

    private Capabilities load() {
        Instant threshold = Instant.now().minus(ONLINE_WINDOW);
        List<WorkerView> workers = jdbc.sql("""
                SELECT worker_id, backends::text AS backends, device_type, capabilities::text AS capabilities, last_seen
                FROM workers ORDER BY last_seen DESC LIMIT 50
                """)
                .query((row, index) -> {
                    Instant lastSeen = row.getObject("last_seen", OffsetDateTime.class).toInstant();
                    return new WorkerView(row.getString("worker_id"), row.getString("backends"), row.getString("device_type"),
                            row.getString("capabilities"), lastSeen, lastSeen.isAfter(threshold));
                })
                .list();
        String generate = properties.backends().generate();
        return new Capabilities(generate, properties.backends().preprocess(), generationLabel(generate, workers), properties.retention().projectDays(), workers);
    }
}
