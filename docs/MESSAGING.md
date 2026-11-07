# Messaging

RabbitMQ transports tasks. PostgreSQL decides what happens. Delivery is at-least-once, and correctness comes from the outbox, leases and fencing, so duplicate compute can occur under failures and is not advertised as impossible.

## Topology (`configs/rabbitmq/definitions.template.json`)

| Queue | Routing key | Consumer |
| --- | --- | --- |
| `motionmirror.validate.v1` | `validate` | CPU validation worker, so checking an upload never needs a GPU |
| `motionmirror.preprocess.mock.v1` | `preprocess.mock` | Mock worker |
| `motionmirror.generate.mock.v1` | `generate.mock` | Mock worker |
| `motionmirror.preprocess.v1` | `preprocess.real` | Real preprocessing worker |
| `motionmirror.generate.wan-animate.v1` | `generate.wan-animate` | GPU worker |
| `motionmirror.dead.*.v1` | From `motionmirror.dlx` | Operators |

All are durable quorum queues with `x-delivery-limit: 5`, `x-max-length: 1000` and `x-overflow: reject-publish`, so a full queue gives explicit backpressure, not silent drops. A one-node local broker is **not highly available**; production needs a replicated deployment.

## Identities

| User | Can do |
| --- | --- |
| `mm-provision` | Administer topology. Not used at runtime |
| `mm-publisher` | Write to `motionmirror.tasks` only |
| `mm-consumer-validate` | Read the validation queue only |
| `mm-consumer-mock` | Read mock queues only |
| `mm-consumer-wan` | Read real queues only |
| `mm-monitor` | Monitoring tag, no data access |

Negative permission tests live in `apps/api/src/test/java/com/motionmirror/jobs/OutboxIT.java`.

## Message contract

`packages/contracts/messages/task-envelope.v1.schema.json`, with fixtures run by JUnit and pytest. It carries identifiers only: never bytes, secrets, signed URLs, prompts or executable arguments. The worker gets the real request from the API after acquiring a lease.

## Flow

1. A job and its outbox row commit in one transaction.
2. `OutboxPublisher` reserves rows in a short transaction with `FOR UPDATE SKIP LOCKED`, then publishes holding no database lock. It uses persistent delivery, confirms and mandatory returns.
3. It marks an event published only after a confirm with no return. Otherwise the event goes back to pending with backoff.
4. The worker validates the envelope and calls `POST /internal/v1/jobs/{id}/acquire`.
5. `acquire` locks the job row and returns a lease, or `409` with a disposition. The request carries an `acquire_id` the worker picks for each delivery and keeps across its own HTTP retries. If a response is lost after the attempt was created, the retry gets that same lease back. Another delivery, even to the same worker, has a new id and is a duplicate.

| Disposition | Meaning | Worker action |
| --- | --- | --- |
| `DUPLICATE` | Already running or finished | Ack, don't execute |
| `OBSOLETE` | An older dispatch generation | Ack, don't execute |
| `CANCELLED` | The job was cancelled | Ack, don't execute |

6. The worker heartbeats from a process separate from the model, reports progress, uploads to its attempt's prefix and calls `complete` or `fail`.
7. It acks only after the outcome is recorded. Malformed messages are rejected without requeue and reach the dead-letter queue.

## Fencing

Each attempt gets a monotonically increasing token. Heartbeat, progress, artifact access, complete and fail all require the current token and an unexpired lease, or they return `409 LEASE_LOST`. `UNIQUE (job_id, name)` on `outputs` is the final guard, so a job can never attach two results.

## Retries and recovery

Spring Boot owns retries. A retryable failure moves to `RETRY_WAIT` with a persisted `next_attempt_at`; the reconciler then creates a new dispatch generation, which makes any earlier message obsolete. `CUDA_OOM`, `INVALID_MEDIA`, `UNSUPPORTED_CAPABILITY`, `MODEL_MISSING`, `TARGET_LOST` and `INVALID_REQUEST` are never retried, and quality is never silently lowered.

The reconciler runs under a PostgreSQL advisory lock, so one replica acts at a time. It expires leases, starts due retries and redispatches queued jobs only after `MM_QUEUED_REDISPATCH_AFTER` (8 hours) of silence, because ordinary queue wait isn't message loss.

## Acknowledgement deadline

AMQP heartbeats don't extend delivery deadlines, so the `long-gpu-attempts` policy sets `consumer-timeout` to `MM_RABBIT_CONSUMER_TIMEOUT_MS`, 6 hours by default. It must exceed `MM_MAX_ATTEMPT_DURATION` (4 hours) plus a 30-minute cleanup margin. The API refuses to start otherwise, and `make doctor` checks the live broker.

**Verified on the pinned RabbitMQ 4.3 image**, in `services/ml-worker/tests/integration/test_broker_deadline.py`:

- [Delivery acknowledgement timeouts are only supported by quorum queues](https://www.rabbitmq.com/docs/consumers) from 4.3, which is why every queue here is a quorum queue.
- A timed-out AMQP 0-9-1 consumer is **cancelled and its message returned**. The channel stays open.
- `aiormq` drops the consumer without any callback, so a worker could sit idle while looking healthy. A watchdog in `consume()` checks every 10 seconds and subscribes again, and a late ack that fails is logged rather than fatal.

## What's tested

`LeaseIT` and `OutboxIT`, on real services: transaction rollback, publish-before-mark crash, broker outage, unroutable message, poison message to DLQ, permissions, concurrent acquire, stale workers, idempotent completion, output prefix checks, cancel races and retry limits.
