package com.motionmirror.cache;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.motionmirror.jobs.JobKind;
import com.motionmirror.jobs.JobRow;
import com.motionmirror.jobs.JobService;
import com.motionmirror.shared.ApiException;
import com.motionmirror.support.Fixtures;
import com.motionmirror.support.Infrastructure;
import com.motionmirror.support.IntegrationTest;
import com.motionmirror.workers.LeaseService;
import com.motionmirror.workers.WorkerDtos;
import java.time.Duration;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;

@IntegrationTest
class RedisBehaviourIT {

    public record Value(String text) {}

    @Autowired MetadataCache cache;
    @Autowired RateLimiter limiter;
    @Autowired StringRedisTemplate redis;
    @Autowired Fixtures fixtures;
    @Autowired LeaseService leases;
    @Autowired JobService jobs;

    private String key() {
        return "test:" + UUID.randomUUID();
    }

    @Test
    void aSecondReadComesFromTheCache() {
        String key = key();
        AtomicInteger loads = new AtomicInteger();
        Value first = cache.getOrLoad(key, Duration.ofMinutes(1), Value.class, () -> new Value("loaded-" + loads.incrementAndGet()));
        Value second = cache.getOrLoad(key, Duration.ofMinutes(1), Value.class, () -> new Value("loaded-" + loads.incrementAndGet()));
        assertThat(first).isEqualTo(second);
        assertThat(loads).hasValue(1);
    }

    @Test
    void anExpiredEntryLoadsAgain() throws Exception {
        String key = key();
        AtomicInteger loads = new AtomicInteger();
        cache.getOrLoad(key, Duration.ofMillis(100), Value.class, () -> new Value("v" + loads.incrementAndGet()));
        Thread.sleep(400);
        cache.getOrLoad(key, Duration.ofMillis(100), Value.class, () -> new Value("v" + loads.incrementAndGet()));
        assertThat(loads).hasValue(2);
    }

    @Test
    void evictionForcesAFreshRead() {
        String key = key();
        AtomicInteger loads = new AtomicInteger();
        cache.getOrLoad(key, Duration.ofMinutes(1), Value.class, () -> new Value("v" + loads.incrementAndGet()));
        cache.evict(key);
        cache.getOrLoad(key, Duration.ofMinutes(1), Value.class, () -> new Value("v" + loads.incrementAndGet()));
        assertThat(loads).hasValue(2);
    }

    @Test
    void concurrentMissesLoadOnce() throws Exception {
        String key = key();
        AtomicInteger loads = new AtomicInteger();
        try (ExecutorService pool = Executors.newFixedThreadPool(8)) {
            for (int index = 0; index < 8; index++) {
                pool.submit(() -> cache.getOrLoad(key, Duration.ofMinutes(1), Value.class, () -> {
                    sleep(100);
                    return new Value("v" + loads.incrementAndGet());
                }));
            }
        }
        assertThat(loads).hasValue(1);
    }

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    @Test
    void theLimiterRejectsAfterTheBurst() {
        UUID user = UUID.randomUUID();
        for (int index = 0; index < 3; index++) {
            limiter.check("test", user, 3);
        }
        assertThatThrownBy(() -> limiter.check("test", user, 3))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo("RATE_LIMITED"));
    }

    @Test
    void oneUsersBurstDoesNotAffectAnother() {
        UUID busy = UUID.randomUUID();
        for (int index = 0; index < 3; index++) {
            limiter.check("test", busy, 3);
        }
        limiter.check("test", UUID.randomUUID(), 3);
    }

    @Test
    void theRedisIdentityCantTouchKeysOutsideItsPrefix() {
        assertThatThrownBy(() -> redis.opsForValue().set("outside", "1")).hasStackTraceContaining("NOPERM");
    }

    @Test
    void withRedisDownCacheReadsFallBackLimitsRefuseAndWorkerCallsContinue() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        JobRow job = fixtures.queuedJob(owner, project, JobKind.PREVIEW);
        WorkerDtos.AcquireResponse attempt = ((LeaseService.Acquired) leases.acquire(job.id(), UUID.randomUUID(), 1, "w", Set.of("mock"))).response();

        var docker = Infrastructure.REDIS.getDockerClient();
        String container = Infrastructure.REDIS.getContainerId();
        docker.pauseContainerCmd(container).exec();
        try {
            Value loaded = cache.getOrLoad(key(), Duration.ofMinutes(1), Value.class, () -> new Value("from-postgres"));
            assertThat(loaded.text()).isEqualTo("from-postgres");

            assertThatThrownBy(() -> limiter.check("test", owner, 100))
                    .isInstanceOfSatisfying(ApiException.class, error -> {
                        assertThat(error.code()).isEqualTo("RATE_LIMITER_UNAVAILABLE");
                        assertThat(error.status().value()).isEqualTo(503);
                    });

            assertThat(leases.heartbeat(job.id(), attempt.attemptId(), attempt.fencingToken()).leaseExpiresAt()).isNotNull();
            assertThat(jobs.cancel(new com.motionmirror.identity.CurrentUser(owner, Set.of("ROLE_USER")), job.id()).state())
                    .isEqualTo(com.motionmirror.jobs.JobState.CANCEL_REQUESTED);
        } finally {
            docker.unpauseContainerCmd(container).exec();
        }
    }
}
