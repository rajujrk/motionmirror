package com.motionmirror.observability;

import com.motionmirror.jobs.JobState;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import java.time.Instant;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * What an operator needs to see at a glance, read from the database because that's the authority. Tags are limited to
 * a fixed list of job states, so no user, project or job can ever reach a metric label.
 */
@Component
public class OperationalMetrics {

    private static final Logger log = LoggerFactory.getLogger(OperationalMetrics.class);
    private static final Duration ONLINE_WINDOW = Duration.ofMinutes(2);

    private final JdbcClient jdbc;
    private final Map<JobState, AtomicLong> jobs = new EnumMap<>(JobState.class);
    private final AtomicLong outboxPending = new AtomicLong();
    private final AtomicLong outboxOldestSeconds = new AtomicLong();
    private final AtomicLong workersOnline = new AtomicLong();

    public OperationalMetrics(JdbcClient jdbc, MeterRegistry metrics) {
        this.jdbc = jdbc;
        for (JobState state : JobState.values()) {
            AtomicLong value = new AtomicLong();
            jobs.put(state, value);
            Gauge.builder("mm.jobs", value, AtomicLong::get).tag("state", state.name()).register(metrics);
        }
        Gauge.builder("mm.outbox.pending", outboxPending, AtomicLong::get).register(metrics);
        Gauge.builder("mm.outbox.oldest.seconds", outboxOldestSeconds, AtomicLong::get).register(metrics);
        Gauge.builder("mm.workers.online", workersOnline, AtomicLong::get).register(metrics);
    }

    @Scheduled(fixedDelayString = "${motionmirror.metrics.refresh-interval:15s}")
    public void refresh() {
        try {
            List<Map<String, Object>> states = jdbc.sql("SELECT state, count(*) AS total FROM generation_jobs GROUP BY state").query().listOfRows();
            jobs.values().forEach(value -> value.set(0));
            for (Map<String, Object> row : states) {
                jobs.get(JobState.valueOf((String) row.get("state"))).set(((Number) row.get("total")).longValue());
            }
            Map<String, Object> outbox = jdbc.sql("""
                    SELECT count(*) AS pending, COALESCE(extract(epoch FROM now() - min(created_at)), 0) AS oldest
                    FROM outbox_events WHERE state <> 'PUBLISHED'
                    """).query().singleRow();
            outboxPending.set(((Number) outbox.get("pending")).longValue());
            outboxOldestSeconds.set(((Number) outbox.get("oldest")).longValue());
            workersOnline.set(jdbc.sql("SELECT count(*) FROM workers WHERE last_seen > :since")
                    .param("since", java.sql.Timestamp.from(Instant.now().minus(ONLINE_WINDOW))).query(Long.class).single());
        } catch (RuntimeException error) {
            // A stale gauge is better than an exception loop, and a database outage has its own alert.
            log.warn("could not refresh operational metrics: {}", error.getClass().getSimpleName());
        }
    }
}
