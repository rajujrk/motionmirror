package com.motionmirror.jobs;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.motionmirror.assets.AssetEntity;
import com.motionmirror.config.AppProperties;
import com.motionmirror.shared.Json;
import com.motionmirror.support.Fixtures;
import com.motionmirror.support.Infrastructure;
import com.motionmirror.support.IntegrationTest;
import com.motionmirror.workers.LeaseService;
import com.rabbitmq.client.Channel;
import com.rabbitmq.client.Connection;
import com.rabbitmq.client.ConnectionFactory;
import com.rabbitmq.client.GetResponse;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;

@IntegrationTest
class OutboxIT {

    private static final String MOCK_QUEUE = "motionmirror.generate.mock.v1";

    @Autowired OutboxStore outbox;
    @Autowired RabbitTemplate rabbit;
    @Autowired AppProperties properties;
    @Autowired Fixtures fixtures;
    @Autowired JobStore jobs;
    @Autowired JobService jobService;
    @Autowired LeaseService leases;
    @Autowired TransactionTemplate transactions;
    @Autowired JdbcClient jdbc;

    OutboxPublisher publisher;
    UUID owner;
    UUID project;
    Connection consumerConnection;
    Channel consumer;

    @BeforeEach
    void setUp() throws Exception {
        publisher = new OutboxPublisher(outbox, rabbit, properties, new SimpleMeterRegistry());
        owner = fixtures.user();
        project = fixtures.project(owner);
        consumerConnection = connect("mm-consumer-mock");
        consumer = consumerConnection.createChannel();
        drain();
        publisher.publishPending();
        drain();
    }

    @AfterEach
    void tearDown() throws Exception {
        consumerConnection.close();
    }

    private static Connection connect(String user) throws Exception {
        ConnectionFactory factory = new ConnectionFactory();
        factory.setHost(Infrastructure.RABBIT.getHost());
        factory.setPort(Infrastructure.RABBIT.getMappedPort(5672));
        factory.setVirtualHost("motionmirror");
        factory.setUsername(user);
        factory.setPassword(Infrastructure.BROKER_PASSWORD);
        return factory.newConnection();
    }

    private void drain() throws Exception {
        for (GetResponse response = consumer.basicGet(MOCK_QUEUE, true); response != null; response = consumer.basicGet(MOCK_QUEUE, true)) {
            // Empty.
        }
    }

    private JsonNode nextMessage() throws Exception {
        for (int attempt = 0; attempt < 20; attempt++) {
            GetResponse response = consumer.basicGet(MOCK_QUEUE, true);
            if (response != null) {
                assertThat(response.getProps().getDeliveryMode()).isEqualTo(2);
                return Json.read(new String(response.getBody(), StandardCharsets.UTF_8));
            }
            Thread.sleep(100);
        }
        return null;
    }

    private long unpublishedFor(UUID jobId) {
        return jdbc.sql("SELECT count(*) FROM outbox_events WHERE job_id = :job AND state <> 'PUBLISHED'").param("job", jobId).query(Long.class).single();
    }

    @Test
    void aJobAndItsDispatchCommitOrFailTogether() {
        UUID[] jobId = new UUID[1];
        assertThatThrownBy(() -> transactions.executeWithoutResult(status -> {
            jobId[0] = fixtures.queuedJob(owner, project, JobKind.PREVIEW).id();
            throw new IllegalStateException("simulated failure before commit");
        })).isInstanceOf(IllegalStateException.class);

        assertThat(jobs.find(jobId[0])).isEmpty();
        assertThat(jdbc.sql("SELECT count(*) FROM outbox_events WHERE job_id = :job").param("job", jobId[0]).query(Long.class).single()).isZero();
    }

    @Test
    void twoSubmissionsRacingWithOneKeyBothGetTheSameJob() throws Exception {
        UUID asset = fixtures.readyAsset(owner, project, AssetEntity.DRIVING_VIDEO);
        var submission = new JobService.Submission(project, JobKind.PREVIEW, "preview@1", "{}", "{}",
                Json.write(Map.of("asset_id", asset.toString(), "source_asset_id", asset.toString())), "double-click", true);
        CountDownLatch inserted = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        CompletableFuture<JobRow> first = CompletableFuture.supplyAsync(() -> transactions.execute(status -> {
            JobRow job = jobService.submit(owner, submission);
            inserted.countDown();
            awaitQuietly(release);
            return job;
        }));
        assertThat(inserted.await(10, TimeUnit.SECONDS)).isTrue();
        CompletableFuture<JobRow> second = CompletableFuture.supplyAsync(() -> jobService.submit(owner, submission));
        Thread.sleep(500); // the second has missed the uncommitted job and now waits on it
        release.countDown();

        assertThat(second.get(20, TimeUnit.SECONDS).id()).isEqualTo(first.get(20, TimeUnit.SECONDS).id());
        assertThat(jdbc.sql("SELECT count(*) FROM generation_jobs WHERE owner_id = :owner AND idempotency_key = 'double-click'")
                .param("owner", owner).query(Long.class).single()).isOne();
    }

    private static void awaitQuietly(CountDownLatch latch) {
        try {
            latch.await(20, TimeUnit.SECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    @Test
    void aPublishedEnvelopeCarriesOnlyIdentifiers() throws Exception {
        JobRow job = fixtures.queuedJob(owner, project, JobKind.PREVIEW);
        publisher.publishPending();

        JsonNode message = nextMessage();
        assertThat(message).isNotNull();
        assertThat(message.path("job_id").asString()).isEqualTo(job.id().toString());
        assertThat(message.path("dispatch_generation").asInt()).isEqualTo(1);
        assertThat(message.propertyNames().stream().toList()).containsExactlyInAnyOrder(
                "schema_version", "event_id", "job_id", "dispatch_generation", "task_type", "backend_id", "config_revision", "created_at");
        assertThat(unpublishedFor(job.id())).isZero();
    }

    @Test
    void aCrashAfterPublishBeforeMarkCausesADuplicateThatAcquireHandles() throws Exception {
        JobRow job = fixtures.queuedJob(owner, project, JobKind.PREVIEW);
        List<OutboxStore.OutboxEvent> reserved = outbox.reserve(50, Duration.ofMillis(100));
        OutboxStore.OutboxEvent event = reserved.stream().filter(row -> row.jobId().equals(job.id())).findFirst().orElseThrow();
        // The process dies here: the message went out, but publication was never recorded.
        assertThat(publisher.send(event)).isNull();
        assertThat(unpublishedFor(job.id())).isEqualTo(1);

        Thread.sleep(200);
        publisher.publishPending();
        assertThat(unpublishedFor(job.id())).isZero();

        JsonNode first = nextMessage();
        JsonNode duplicate = nextMessage();
        assertThat(first.path("event_id")).isEqualTo(duplicate.path("event_id"));

        Set<String> mock = Set.of("mock");
        UUID eventId = UUID.fromString(first.path("event_id").asString());
        assertThat(leases.acquire(job.id(), eventId, 1, "w1", mock)).isInstanceOf(LeaseService.Acquired.class);
        assertThat(leases.acquire(job.id(), eventId, 1, "w2", mock)).isInstanceOf(LeaseService.Refused.class);
    }

    @Test
    void anUnroutableMessageStaysInTheOutbox() {
        JobRow job = fixtures.queuedJob(owner, project, JobKind.PREVIEW);
        jdbc.sql("UPDATE outbox_events SET routing_key = 'generate.nothing-bound-here' WHERE job_id = :job").param("job", job.id()).update();

        publisher.publishPending();

        assertThat(unpublishedFor(job.id())).isEqualTo(1);
        String error = jdbc.sql("SELECT last_error FROM outbox_events WHERE job_id = :job").param("job", job.id()).query(String.class).single();
        assertThat(error).contains("unroutable");
        jdbc.sql("DELETE FROM outbox_events WHERE job_id = :job").param("job", job.id()).update();
    }

    @Test
    void aBrokerOutageKeepsEventsDurableUntilItReturns() throws Exception {
        JobRow job = fixtures.queuedJob(owner, project, JobKind.PREVIEW);
        var docker = Infrastructure.RABBIT.getDockerClient();
        String container = Infrastructure.RABBIT.getContainerId();
        docker.pauseContainerCmd(container).exec();
        try {
            publisher.publishPending();
            assertThat(unpublishedFor(job.id())).isEqualTo(1);
            assertThat(jobs.find(job.id()).orElseThrow().state()).isEqualTo(JobState.QUEUED);
        } finally {
            docker.unpauseContainerCmd(container).exec();
        }
        jdbc.sql("UPDATE outbox_events SET next_attempt_at = now() WHERE job_id = :job").param("job", job.id()).update();
        for (int attempt = 0; attempt < 30 && unpublishedFor(job.id()) > 0; attempt++) {
            publisher.publishPending();
            Thread.sleep(500);
            jdbc.sql("UPDATE outbox_events SET next_attempt_at = now() WHERE job_id = :job AND state = 'PENDING'").param("job", job.id()).update();
        }
        assertThat(unpublishedFor(job.id())).isZero();
    }

    @Test
    void aPoisonMessageGoesToTheDeadLetterQueueNotARequeueLoop() throws Exception {
        rabbit.convertAndSend(OutboxPublisher.EXCHANGE, "generate.mock", "not json at all");
        GetResponse delivery = null;
        for (int attempt = 0; attempt < 20 && delivery == null; attempt++) {
            delivery = consumer.basicGet(MOCK_QUEUE, false);
            Thread.sleep(50);
        }
        assertThat(delivery).isNotNull();
        consumer.basicReject(delivery.getEnvelope().getDeliveryTag(), false);

        try (Connection provision = connect("mm-provision"); Channel channel = provision.createChannel()) {
            GetResponse dead = null;
            for (int attempt = 0; attempt < 40 && dead == null; attempt++) {
                dead = channel.basicGet("motionmirror.dead.generate.mock.v1", true);
                Thread.sleep(100);
            }
            assertThat(dead).isNotNull();
            assertThat(new String(dead.getBody(), StandardCharsets.UTF_8)).contains("not json");
        }
    }

    @Test
    void aMockConsumerIdentityCannotReadTheGpuQueue() throws Exception {
        try (Channel channel = consumerConnection.createChannel()) {
            assertThatThrownBy(() -> channel.basicGet("motionmirror.generate.wan-animate.v1", true))
                    .hasStackTraceContaining("ACCESS_REFUSED");
        } catch (com.rabbitmq.client.AlreadyClosedException expected) {
            // The broker closes the channel after refusing access.
        }
    }

    @Test
    void thePublisherIdentityCannotConsume() throws Exception {
        try (Connection publisherConnection = connect("mm-publisher"); Channel channel = publisherConnection.createChannel()) {
            assertThatThrownBy(() -> channel.basicGet(MOCK_QUEUE, true)).hasStackTraceContaining("ACCESS_REFUSED");
        } catch (com.rabbitmq.client.AlreadyClosedException expected) {
            // The broker closes the channel after refusing access.
        }
    }
}
