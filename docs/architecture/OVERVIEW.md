# Architecture overview

Next.js is the UI. A Spring Boot monolith is the system of record, backed by PostgreSQL. RabbitMQ carries work to Python workers, which act only under a fenced lease from the API. Redis caches and rate-limits but is never authoritative. Media lives in private S3-compatible storage.

```text
Browser ── HTTPS ──> Caddy ──> Next.js (UI, no business logic)
                       │
                       ├─────> Spring Boot API ───> PostgreSQL (system of record, outbox, sessions)
                       │             │  │
                       │             │  └─────────> Redis (presets, capabilities, rate limits)
                       │             │
                       └─> Keycloak  │ publishes after commit
                                     v
                                 RabbitMQ
                    ┌────────────────┼──────────────────────┐
                    v                v                      v
           validate worker    real preprocess         real generation
              (CPU, ClamAV)   (SAM 2, ViTPose)         (Wan-Animate)
                    │                │                      │
                    └── lease, heartbeat, complete over HTTPS ──> API
                    └── signed URLs only ───────────────────────> Object storage
```

## A job's life

1. The API writes the job and its outbox event in **one transaction**, so a job can't exist without its message, or the reverse.
2. The publisher sends the event with no lock held, and marks it published only after the broker confirms routing. A crash in between sends it again, which is safe.
3. The message holds identifiers only. The worker asks the API for a lease, and the API gives it a fencing token and the job's inputs. A duplicate or stale message gets a refusal and is dropped.
4. The worker's parent process downloads inputs and sends heartbeats. A child process does the work and sees only local files, so a crash, hang or out-of-memory can't take the supervisor with it.
5. Outputs go to an attempt-specific prefix through signed URLs. Completion is fenced, so a worker that lost its lease can't overwrite the attempt that replaced it.
6. If heartbeats stop, the lease expires and the reconciler schedules another attempt.

The queue never decides anything. PostgreSQL does.

## Decisions

Recorded in `docs/architecture/decisions/`: Diffusers as the inference runtime, DiffSynth as the trainer, selection-driven preprocessing, SeaweedFS replacing the archived MinIO, platform support, the dataset licence policy and monorepo tooling.

## Preprocessing

The official Wan script assumes one person in the shot, so it can't choose one dancer among several. Here the user's marks seed SAM 2, the mask gives ViTPose its box, and the hidden-dancer case can never let the pose model pick someone else. Output keeps the official format: `src_pose`, `src_face`, `src_bg` and `src_mask`.

Memory is bounded by clip length: tracking runs in 120-frame chunks seeded from the previous chunk's mask, and controls are written in one streaming pass. Chunking cost no accuracy on 240 real frames (IoU 0.954 against 0.952 unchunked).

## Device labels

| Label | Meaning |
| --- | --- |
| `mock` | No model ran. Visibly labelled in the UI, the manifest and every frame |
| `experimental` | A model ran, on a Mac or CPU. Can't satisfy an acceptance gate |
| `real` | A model ran on a recorded CUDA device |

`select_device()` fails rather than moving a CUDA path to CPU or MPS.
