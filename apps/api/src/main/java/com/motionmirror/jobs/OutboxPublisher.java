package com.motionmirror.jobs;

import com.motionmirror.config.AppProperties;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.core.Message;
import org.springframework.amqp.core.MessageBuilder;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Publishes the outbox with confirms and mandatory returns. A crash between publish and mark causes a
 * duplicate on the next pass, so delivery is at-least-once; acquire handles duplicates.
 */
@Component
@ConditionalOnProperty(name = "motionmirror.jobs.outbox-enabled", havingValue = "true", matchIfMissing = true)
public class OutboxPublisher {

    public static final String EXCHANGE = "motionmirror.tasks";
    private static final Logger log = LoggerFactory.getLogger(OutboxPublisher.class);
    private static final int BATCH = 50;

    private final OutboxStore outbox;
    private final RabbitTemplate rabbit;
    private final AppProperties.Jobs config;
    private final MeterRegistry metrics;

    public OutboxPublisher(OutboxStore outbox, RabbitTemplate rabbit, AppProperties properties, MeterRegistry metrics) {
        this.outbox = outbox;
        this.rabbit = rabbit;
        this.config = properties.jobs();
        this.metrics = metrics;
        Gauge.builder("mm.outbox.oldest.seconds", outbox, OutboxStore::oldestUnpublishedSeconds).register(metrics);
        Gauge.builder("mm.outbox.unpublished", outbox, OutboxStore::countUnpublished).register(metrics);
    }

    @Scheduled(fixedDelayString = "${motionmirror.jobs.outbox-poll}")
    public void publishPending() {
        List<OutboxStore.OutboxEvent> events;
        try {
            events = outbox.reserve(BATCH, config.outboxReservation());
        } catch (RuntimeException error) {
            log.warn("outbox reservation failed: {}", error.getClass().getSimpleName());
            return;
        }
        for (OutboxStore.OutboxEvent event : events) {
            publishOne(event);
        }
    }

    void publishOne(OutboxStore.OutboxEvent event) {
        String failure = send(event);
        if (failure == null) {
            outbox.markPublished(event.id());
            metrics.counter("mm.outbox.published").increment();
            return;
        }
        metrics.counter("mm.outbox.publish.failed").increment();
        log.warn("event {} not published: {}", event.id(), failure);
        outbox.markFailed(event.id(), failure, backoff(event.publishAttempts()));
    }

    /** Returns null only after the broker confirmed the message and it was routed to a queue. */
    String send(OutboxStore.OutboxEvent event) {
        Message message = MessageBuilder.withBody(event.payload().getBytes(StandardCharsets.UTF_8))
                .setContentType("application/json")
                .setContentEncoding("utf-8")
                .setDeliveryMode(MessageDeliveryMode.PERSISTENT)
                .setMessageId(event.id().toString())
                .build();
        CorrelationData correlation = new CorrelationData(event.id().toString());
        try {
            rabbit.send(EXCHANGE, event.routingKey(), message, correlation);
            CorrelationData.Confirm confirm = correlation.getFuture()
                    .get(config.publishConfirmTimeout().toMillis(), TimeUnit.MILLISECONDS);
            if (correlation.getReturned() != null) {
                return "unroutable: " + correlation.getReturned().getReplyText();
            }
            return confirm.ack() ? null : "broker refused: " + confirm.reason();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return "interrupted";
        } catch (Exception error) {
            return error.getClass().getSimpleName();
        }
    }

    private static Duration backoff(int attempts) {
        return Duration.ofSeconds(Math.min(60, 1L << Math.min(attempts, 6)));
    }
}
