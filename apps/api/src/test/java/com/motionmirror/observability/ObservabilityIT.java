package com.motionmirror.observability;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.motionmirror.jobs.JobKind;
import com.motionmirror.jobs.JobRow;
import com.motionmirror.jobs.JobService;
import com.motionmirror.shared.Json;
import com.motionmirror.support.Fixtures;
import com.motionmirror.support.IntegrationTest;
import com.motionmirror.workers.LeaseService;
import com.motionmirror.workers.WorkerDtos;
import io.micrometer.core.instrument.Meter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Tag;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.micrometer.metrics.test.autoconfigure.AutoConfigureMetrics;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

@IntegrationTest
@AutoConfigureMetrics
@ExtendWith(OutputCaptureExtension.class)
@TestPropertySource(properties = "motionmirror.metrics-token=" + ObservabilityIT.METRICS_TOKEN)
class ObservabilityIT {

    static final String METRICS_TOKEN = "test-metrics-token";

    private static final Pattern UUID_LIKE = Pattern.compile("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-");
    private static final Pattern PERSONAL = Pattern.compile("@|Bearer |X-Amz-Signature");

    @Autowired MockMvc mvc;
    @Autowired MeterRegistry metrics;
    @Autowired OperationalMetrics operational;
    @Autowired Fixtures fixtures;
    @Autowired JobService jobs;
    @Autowired LeaseService leases;
    @Autowired JdbcClient jdbc;

    @Test
    void jobStatesReachTheMetrics() {
        UUID owner = fixtures.user();
        fixtures.queuedJob(owner, fixtures.project(owner), JobKind.PREVIEW);
        operational.refresh();
        assertThat(metrics.get("mm.jobs").tag("state", "QUEUED").gauge().value()).isGreaterThanOrEqualTo(1);
    }

    @Test
    void anUnpublishedEventShowsItsAge() {
        UUID owner = fixtures.user();
        JobRow job = fixtures.queuedJob(owner, fixtures.project(owner), JobKind.PREVIEW);
        jdbc.sql("UPDATE outbox_events SET created_at = now() - interval '10 minutes', state = 'PENDING' WHERE job_id = :job")
                .param("job", job.id()).update();
        operational.refresh();
        assertThat(metrics.get("mm.outbox.oldest.seconds").gauge().value()).isGreaterThanOrEqualTo(600);
        jdbc.sql("UPDATE outbox_events SET state = 'PUBLISHED' WHERE job_id = :job").param("job", job.id()).update();
    }

    @Test
    void theScrapeEndpointNeedsTheToken() throws Exception {
        mvc.perform(get("/actuator/prometheus")).andExpect(status().is4xxClientError());
        mvc.perform(get("/actuator/prometheus").header("Authorization", "Bearer wrong")).andExpect(status().is4xxClientError());
    }

    @Test
    void theScrapeEndpointWorksWithTheToken() throws Exception {
        operational.refresh();
        String scrape = mvc.perform(get("/actuator/prometheus").header("Authorization", "Bearer " + METRICS_TOKEN))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        assertThat(scrape).contains("mm_jobs").contains("mm_outbox_pending").contains("mm_workers_online");
    }

    @Test
    void otherActuatorEndpointsStayClosed() throws Exception {
        mvc.perform(get("/actuator/env")).andExpect(status().is4xxClientError());
        mvc.perform(get("/actuator/heapdump")).andExpect(status().is4xxClientError());
    }

    @Test
    void readinessAndLivenessAreSeparateAndNeitherSaysMuch() throws Exception {
        String ready = mvc.perform(get("/actuator/health/readiness")).andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        String live = mvc.perform(get("/actuator/health/liveness")).andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        assertThat(Json.read(ready).path("status").asString()).isEqualTo("UP");
        assertThat(Json.read(live).path("status").asString()).isEqualTo("UP");
        assertThat(ready + live).doesNotContain("postgres").doesNotContain("password");
    }

    @Test
    void noMetricLabelCanIdentifyAPersonOrAJob() {
        UUID owner = fixtures.user();
        fixtures.queuedJob(owner, fixtures.project(owner), JobKind.PREVIEW);
        operational.refresh();
        for (Meter meter : metrics.getMeters()) {
            for (Tag tag : meter.getId().getTags()) {
                // The URI tag is Spring's route template, which is safe, but a real id would mean it had stopped being one.
                assertThat(UUID_LIKE.matcher(tag.getValue()).find())
                        .as("%s has an id in its %s label", meter.getId().getName(), tag.getKey()).isFalse();
                assertThat(PERSONAL.matcher(tag.getValue()).find())
                        .as("%s has personal data in its %s label", meter.getId().getName(), tag.getKey()).isFalse();
            }
        }
    }

    @Test
    void promptsAndFailureDetailsStayOutOfTheLogs(CapturedOutput output) {
        String secret = "a-very-private-prompt-" + UUID.randomUUID();
        UUID owner = fixtures.user();
        JobRow job = jobs.submit(owner, new JobService.Submission(fixtures.project(owner), JobKind.PREVIEW, "preview@2",
                Json.write(Map.of("positive_prompt", secret)), Json.write(Map.of("positive_prompt", secret, "seed", 1)),
                "{}", null, true));
        var attempt = ((LeaseService.Acquired) leases.acquire(job.id(), UUID.randomUUID(), 1, "w", Set.of(job.backendId()))).response();
        leases.fail(job.id(), new WorkerDtos.FailRequest(attempt.attemptId(), attempt.fencingToken(), "INTERNAL_ERROR", secret, false));
        assertThat(output.getAll()).doesNotContain(secret);
    }
}
