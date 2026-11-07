package com.motionmirror.cache;

import com.motionmirror.shared.Json;
import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import io.micrometer.core.instrument.MeterRegistry;

/**
 * Cache-aside for bounded metadata. It is an optimization only: when Redis is down, reads go to the
 * loader and an alert-worthy counter increases. Keys are versioned so a schema change can't read old data.
 */
@Component
public class MetadataCache {

    static final String PREFIX = "mm:cache:v1:";
    private static final Logger log = LoggerFactory.getLogger(MetadataCache.class);

    private final StringRedisTemplate redis;
    private final MeterRegistry metrics;
    private final ConcurrentHashMap<String, ReentrantLock> loading = new ConcurrentHashMap<>();

    public MetadataCache(StringRedisTemplate redis, MeterRegistry metrics) {
        this.redis = redis;
        this.metrics = metrics;
    }

    public <T> T getOrLoad(String key, Duration ttl, Class<T> type, Supplier<T> loader) {
        String fullKey = PREFIX + key;
        T cached = read(fullKey, type);
        if (cached != null) {
            metrics.counter("mm.cache", "result", "hit").increment();
            return cached;
        }
        // One loader per key on this instance, so an expiry doesn't stampede PostgreSQL.
        ReentrantLock lock = loading.computeIfAbsent(fullKey, ignored -> new ReentrantLock());
        lock.lock();
        try {
            T again = read(fullKey, type);
            if (again != null) {
                metrics.counter("mm.cache", "result", "hit").increment();
                return again;
            }
            metrics.counter("mm.cache", "result", "miss").increment();
            T loaded = loader.get();
            write(fullKey, loaded, ttl);
            return loaded;
        } finally {
            lock.unlock();
            loading.remove(fullKey, lock);
        }
    }

    public void evict(String key) {
        try {
            redis.delete(PREFIX + key);
        } catch (RuntimeException error) {
            metrics.counter("mm.cache", "result", "error").increment();
            log.warn("cache eviction failed for one key: {}", error.getClass().getSimpleName());
        }
    }

    private <T> T read(String fullKey, Class<T> type) {
        try {
            String value = redis.opsForValue().get(fullKey);
            return value == null ? null : Json.read(value, type);
        } catch (RuntimeException error) {
            metrics.counter("mm.cache", "result", "error").increment();
            return null;
        }
    }

    private void write(String fullKey, Object value, Duration ttl) {
        long jitter = ThreadLocalRandom.current().nextLong(Math.max(1, ttl.toMillis() / 10));
        try {
            redis.opsForValue().set(fullKey, Json.write(value), ttl.plusMillis(jitter));
        } catch (RuntimeException error) {
            metrics.counter("mm.cache", "result", "error").increment();
        }
    }
}
