# Implementation status

Updated 2026-11-07. Resume from this file and `docs/superpowers/plans/2026-11-06-motionmirror-master-plan.md`.
Tiers: **P0** core flow, **P1** hardening gate, **P2** deferred.

**GPU validation performed: none.** Real generation, GPU benchmarks, real LoRA training and the MVP and training gates are `pending GPU validation`.
**Ubuntu, Windows/WSL2 and CI have not been run.** Everything below was run on one Apple M1 Pro with 16 GB.

## Where it stands

The whole application runs end to end on this Mac. A user signs in, uploads a clip, selects a dancer, gets real masks and poses from a native worker (labelled `experimental`), adds photos, runs a job, downloads a result visibly labelled `MOCK / NO AI REPLACEMENT`, and deletes it. Jobs survive a killed worker, an API restart, a frozen worker and a storage outage. Real replacement video needs a CUDA worker, which hasn't been run.

| Gate | Status | Evidence |
| --- | --- | --- |
| Local foundation (Phase 1 exit) | **Met on this Mac** | `journey.spec.ts`, `recovery.spec.ts`, the `*IT` suites |
| MVP gate (brief §16) | `pending GPU validation`. Every item a CPU can test is evidenced | Below |
| Training gate (brief §16) | `pending GPU validation`. Data, licence gate, smoke runs and the adapter API are evidenced | Below |
| Ubuntu, Windows/WSL2, CI | Written, not run | `docs/LOCAL_DEVELOPMENT.md`, `.github/workflows/` |

## Tasks

| Task | Tier | Status | Evidence | Left |
| --- | --- | --- | --- | --- |
| T0.1 Repo baseline | P0 | Done | Documents in `docs/product/`, ADRs 0001-0007 | |
| T0.2 Diffusers contract spike | P0 | Done | Tiny-model tests on CPU and MPS, `docs/MODEL_CAPABILITIES.md` | |
| T0.3 Apple Silicon spike | P0 | Done | Dtypes, SAM 2 on MPS and CPU, ViTPose on CPU and CoreML. Real 14B from Q2_K GGUF generated at a toy size in 475 s: `docs/MAC_APPLE_SILICON.md` | |
| T0.4 CUDA CLI spike | P0 | Code done | `spike_infer` end to end on the tiny pipeline | The real run: `pending GPU validation` |
| T0.5 Training-path spike | P0 | Done | DiffSynth `08fdc0da` read from source, key-format tests, `docs/TRAINING.md` | Pairing checked on a real run |
| T0.6 Dataset spike | P0 | Done | 48.6 GB archive, layout, `License.pdf`, checksums of `download/`, samples built | |
| T1.1 Mono-repo and Compose | P0 | Done | Compose profiles, `make doctor`, memory and disk per profile in `docs/MAC_APPLE_SILICON.md` | |
| T1.2 Contracts | P0 | Done | Shared fixtures run by JUnit and pytest, OpenAPI exported | **P1:** nothing validates `asyncapi.yaml`, and CI has no OpenAPI drift check |
| T1.3 API, schema, identity | P0 | Done | Security chains, Flyway V1-V5, real Keycloak login in every browser journey | |
| T1.4 Projects and assets | P0 | Done | `UploadIT` on SeaweedFS | |
| T1.5 Jobs, outbox, topology | P0 | Done | `OutboxIT`, `docs/MESSAGING.md` | |
| T1.6 Leases and reconciler | P0 | Done | `LeaseIT`, recovery journeys | **P1:** the lease reconciler is tested end to end only, with no dedicated integration test |
| T1.7 Redis and quotas | P0 | Done | `RedisBehaviourIT`, `docs/REDIS.md` | Valkey, the production choice, hasn't been tested |
| T1.8 Worker and mock backend | P0 | Done | Worker unit, contract and broker-deadline tests | |
| T1.9 Web journey | P0 | Done | 7 browser journeys | |
| T2.1 Selection | P0 | Done | Coordinate round-trip tests, browser journey | |
| T2.2 Real preprocessing | P0 | Done | Real SAM 2 and ViTPose on real clips, chunked to 27 s, IoU 0.954 against the dataset's masks | |
| T2.3 References and preview | P0 | Done | Browser journeys, photo removal journey | |
| T2.4 Wan-Animate backend | P0 | Code done | Tiny-pipeline contract tests: long clips, silence, several audio tracks, variable frame rate | Real output: `pending GPU validation` |
| T2.5 GPU path | P0 | Code done | `Dockerfile.cuda`, `make up-gpu`, `smoke-gpu`, `benchmark-gpu`, `worker-native-mac` (run) | `make build-worker-amd64` not run. GPU runs pending |
| T3.1 Long clips | P0 | Done on tiny and mock | Contract tests, 27 s real preprocessing | Real 30 s run: `pending GPU validation` |
| T3.2 Job control | P0 | Done | Cut rules measured on real joins, `make test-failure-recovery` (4 scenarios) | |
| T3.3 Lifecycle | P0 | Done | `DeletionIT` including restore reconciliation, deletion journeys | |
| T4.1 Datasets | P0 | Done | Licence gate, group splits, near-duplicates, real samples in all three splits | |
| T4.2 Training | P0 | Smoke done | `cpu-smoke` and `mac-mps` on real samples, refusals for `train-lora` | Real run: owner's hardware decision, `pending GPU validation` |
| T4.3 Evaluation | P0 | Code done | Refuses non-CUDA evidence and an empty evaluation set | Real evaluation: `pending GPU validation` |
| T4.4 Adapter registry | P0 | Done | `AdapterLifecycleIT`, research adapters can't be promoted | Live device-flow `make adapter-login` not run |
| T5.1 Deployment artifacts | P1 | Provided, locally validated | Hardened production Compose, startup checks, Runpod runbook | Nothing deployed |
| T5.2 Observability | P1 | Done | Traces across API and worker, metrics behind a token, alerts loaded, dashboard provisioned | |
| T5.3 Security and CI | P1 | Done locally | `make scan` exits 0, ClamAV refuses EICAR in the browser | `make test-load` not run (needs a session cookie and CSRF token). CI not run |
| T5.4 Documentation and handoff | P1 | Done | Every document in brief §18, checked by `tests/tools/test_ci.py` | |

## Test results

Run on 2026-11-07 on this Mac, after the branch review's fixes.

| Suite | Command | Result |
| --- | --- | --- |
| Repository tooling | `make test-tools` | 58 passed |
| API unit | `make test` | 50 passed |
| API integration, real PostgreSQL, RabbitMQ, Redis, SeaweedFS | `make test-integration` | 126 passed |
| Worker unit and contract, tiny Wan model on CPU and MPS | `make test` | 279 passed, 6 skipped (real weights or a CUDA GPU needed) |
| Worker integration | `make test-integration` | 3 passed |
| Training | `make test` | 93 passed |
| Web | `make test` | 25 passed |
| Browser journeys, including the virus-scanner refusal | `MM_E2E_CLAMAV=1 make test-e2e` | 8 passed |
| Failure recovery through the running stack | `make test-failure-recovery` | 4 scenarios passed, plus 40 worker tests |
| Scanners | `make scan` | No leaks, 0 Semgrep findings, Trivy clean at HIGH and CRITICAL |
| Real tracking through the browser and a native worker | `native.spec.ts` | Passed earlier the same day. Not rerun after the review fixes, whose runner changes the mock journeys and recovery runs exercise |

The first browser run after restarting failed 5 tests because a stale ClamAV container held a deleted network, so the validation worker never started. Recreating the two containers fixed it, and the same code then passed.

### Branch review

`/code-review high` over `main..HEAD` reported 10 findings. Nine were real and are fixed, each with a test that failed first. One half of finding 6, manifests written by older code, describes files that don't exist anywhere: this Mac had none before that code ran, and there's no other checkout. The review missed one more, found while checking these: plugin folders holding command history were copied into the worker image. That's fixed, and no image was ever pushed.

## Known issues and risks

- **The edit region is a rectangle.** On the measured clip it covered 53% of the frame against the dancer's 32%, so anything else inside it is regenerated. See `docs/BENCHMARKS.md`.
- **Splits are by compilation, not by person.** The dataset has no person ids, so one person in two compilations could leak across splits.
- **The trainer's frame pairing is inferred** from its code, and needs checking on a real run.
- **The GGUF result on this Mac** is one run at the smallest size, from blank inputs. It shows the quantized model runs, not that it replaces a person well. See `docs/MAC_APPLE_SILICON.md`.
- `JobService.cancel` now refuses file checks. The web app never offered that, so nothing visible changes.
- `.env`, Compose volumes, `.data/` (about 49 GB) and `.models/` (about 18 GB) are local and git-ignored.

## Blockers

| Blocker | Affects | Needed |
| --- | --- | --- |
| No NVIDIA GPU available | Real generation, benchmarks, real LoRA, the MVP and training gates | A CUDA machine |
| DiffSynth says one 80 GB GPU can't train this LoRA, and its authors tested on 8 | Real training cost | The owner's hardware and budget decision |
| TikTok licence: "will not be transferred to outside parties without the authors' permission" | Copying the data to a rented GPU | Ask the authors first |
| No git remote, `gh` absent, GitHub connector failed | Running CI on Ubuntu, macOS and Windows | A pushed repository |

## Before production

- Check that the Amazon MQ broker isn't publicly accessible, and that the per-queue `consumer-timeout` policy applies there.
- Check Valkey with `noeviction` and the limiter's Lua script on ElastiCache.
- Run `make test-load` against a signed-in session.

## Next steps

1. On a CUDA machine: `make up-gpu`, `make smoke-gpu`, `make benchmark-gpu`, then the 30-second acceptance run.
2. Push the repository so CI runs on Ubuntu, macOS and Windows.
3. Ask the TikTok dataset's authors about transfer before any rented-GPU training. Then decide hardware and run `make train-lora`.
4. Close the P1 items above: AsyncAPI validation, an OpenAPI drift check, a dedicated reconciler test.
