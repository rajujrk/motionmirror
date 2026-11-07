# Observability

```bash
make observability-up    # the mock stack, plus Prometheus, Tempo and Grafana
```

| Service | Address | Notes |
| --- | --- | --- |
| Grafana | <http://localhost:53000> | User `admin`, password `MM_GRAFANA_ADMIN_PASSWORD` in `.env`. Open the "MotionMirror overview" dashboard |
| Prometheus | <http://localhost:59090> | Check Status > Targets: `api` and `rabbitmq` should be up |
| Tempo | <http://localhost:53200> | Search traces from Grafana's Explore page |

All of them bind to loopback.

## Metrics

| Metric | Meaning |
| --- | --- |
| `mm_jobs{state}` | Jobs in each state, from the database |
| `mm_outbox_pending`, `mm_outbox_oldest_seconds` | Events not yet confirmed by the broker, and how long the oldest has waited |
| `mm_workers_online` | Workers registered in the last two minutes |
| `mm_lease_expired_total`, `mm_jobs_redispatched_total` | Attempts that stopped reporting, and jobs sent again |
| `mm_cache_total{result}`, `mm_ratelimit_total{result}` | Redis behaviour, including errors |
| `rabbitmq_queue_messages{queue}` | Queue depth, including `motionmirror.dead.*` |
| `http_server_requests_seconds_*` | API rate and latency |

Labels are limited to fixed sets, so no user, project or job id can reach one. `ObservabilityIT` checks every registered metric for this.

## Traces

Submitting a job starts a trace. The API puts `traceparent` in the task envelope, and the worker's `attempt` span joins it, even when the job waited a long time in the queue. Stage changes are span events, and worker calls to the API carry the context too. Tracing is off unless `MM_TRACING_ENABLED=true` on the API and `MM_OTLP_TRACES_ENDPOINT` on the worker. Spans hold job ids, task types, backends, stages and outcomes, and never prompts, URLs or tokens (`test_telemetry.py`).

## Alerts

`infra/observability/prometheus/alerts.yml` holds provisional thresholds. Nothing has run at real load, so refine them from measured baselines.

| Alert | First thing to check |
| --- | --- |
| `ApiDown` | `docker compose logs api` |
| `BrokerDown` | The broker's status. Events wait safely in the outbox, and nothing is lost yet |
| `OutboxBacklog` | Broker connectivity and the publisher's permissions |
| `JobsWaitingWithNoWorker` | Whether the GPU Pod is running, then `make worker-check` |
| `LeasesExpiring` | Worker logs for crashes or out-of-memory kills |
| `DeadLetterQueueNotEmpty` | Inspect the messages before replaying anything, see `docs/MESSAGING.md` |
| `CacheErrors` | Redis. Reads fall through to PostgreSQL, and new expensive work may be refused |

## Health

| Endpoint | Means | Used for |
| --- | --- | --- |
| `/actuator/health/liveness` | The process works. Depends on nothing else | Restarting. A broker outage must never restart a healthy API |
| `/actuator/health/readiness` | The database answers | Sending traffic |

Neither says more than up or down. The proxy routes no `/actuator` path, and `/actuator/prometheus` also needs `MM_METRICS_TOKEN`, so it's closed unless one is set. Prometheus reads the token from tmpfs. Worker model readiness is reported by `make smoke-gpu`, and a missing model fails the job with `MODEL_MISSING`.

## Log limits

Production Compose caps each container's logs at 5 files of 50 MB. Prometheus keeps 7 days and Tempo 24 hours locally.

## Not measured yet

GPU memory, real generation timings and Redis eviction rates need a GPU worker and real load. See `docs/BENCHMARKS.md`.
