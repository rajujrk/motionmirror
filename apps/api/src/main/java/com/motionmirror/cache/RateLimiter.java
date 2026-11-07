package com.motionmirror.cache;

import com.motionmirror.shared.ApiException;
import io.micrometer.core.instrument.MeterRegistry;
import java.util.List;
import java.util.UUID;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

/**
 * Atomic per-minute burst limit. Without Redis, new expensive work is refused with a retriable error, so limits
 * are never silently removed. Cancel, results and worker calls don't pass through here, and the hard spending
 * boundary is the PostgreSQL quota, which a Redis restart can't reset.
 */
@Component
public class RateLimiter {

    private static final DefaultRedisScript<Long> SCRIPT = new DefaultRedisScript<>("""
            local count = redis.call('INCR', KEYS[1])
            if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
            return count
            """, Long.class);
    private static final int WINDOW_SECONDS = 60;

    private final StringRedisTemplate redis;
    private final MeterRegistry metrics;

    public RateLimiter(StringRedisTemplate redis, MeterRegistry metrics) {
        this.redis = redis;
        this.metrics = metrics;
    }

    public void check(String scope, UUID userId, int perMinute) {
        Long count;
        try {
            count = redis.execute(SCRIPT, List.of("mm:limit:v1:" + scope + ":" + userId), String.valueOf(WINDOW_SECONDS));
        } catch (RuntimeException unavailable) {
            metrics.counter("mm.ratelimit", "result", "unavailable").increment();
            throw ApiException.unavailable("RATE_LIMITER_UNAVAILABLE", "This action is briefly unavailable. Try again shortly");
        }
        if (count != null && count > perMinute) {
            metrics.counter("mm.ratelimit", "result", "rejected").increment();
            throw ApiException.tooMany("RATE_LIMITED", "Too many requests. Wait a minute and try again");
        }
    }
}
