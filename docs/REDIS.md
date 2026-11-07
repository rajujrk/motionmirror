# Redis

A performance and rate-limiting layer. It is never the authority for jobs, authorization, idempotency or sessions, and sessions live in PostgreSQL through Spring Session JDBC.

## Key namespaces

| Prefix | Purpose | TTL |
| --- | --- | --- |
| `mm:cache:v1:presets:*` | Current preset list, per visibility | 5 min plus up to 10% jitter |
| `mm:cache:v1:capabilities` | Worker capabilities | 20 s plus jitter |
| `mm:limit:v1:<scope>:<user>` | Per-minute burst counters | 60 s |

The `v1` in each key is a schema version, so a format change can't read old data.

## Security

`configs/redis/users.template.acl` turns the default user off. `mm-api` can only touch `mm:*` keys and can't run `FLUSHALL`, `FLUSHDB`, `KEYS`, `CONFIG` or `SHUTDOWN`. Workers have no Redis access. Local development is plaintext on loopback. A deployment must use TLS and meet the [Redis security guidance](https://redis.io/docs/latest/operate/oss_and_stack/management/security/).

## Memory

Local development uses one 128 MB `noeviction` instance. Production should run a separate non-evicting instance for rate limits, because Redis logical databases don't isolate eviction policy.

## Failure behaviour

| Redis is down | Behaviour |
| --- | --- |
| Cached reads | Fall back to PostgreSQL. `mm.cache{result=error}` increases for alerting |
| Upload intents and job submissions | Refused with `503 RATE_LIMITER_UNAVAILABLE` and `Retry-After`. Limits are never silently removed |
| Cancel, results, worker heartbeat and completion | Unaffected |
| Hard per-user job quota | Unaffected. It is enforced transactionally in PostgreSQL, so a Redis restart can't reset the spending boundary |

## Stampede control

One loader per key per API instance. Across several instances a brief overlap is possible, which is bounded by the instance count.

## What's tested

`RedisBehaviourIT` on real Redis: hit, expiry, eviction, concurrent misses, burst limits, per-user isolation, ACL refusal, and a paused Redis with heartbeat and cancel still working.

## Reset effects

Clearing Redis loses only cached metadata and current rate-limit windows. Nothing durable is lost.
