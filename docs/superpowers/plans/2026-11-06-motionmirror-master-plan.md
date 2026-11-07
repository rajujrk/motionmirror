# MotionMirror Implementation Plan

> **Execution:** native. I act as each role in the brief's §1, one after another, as you asked. Tasks use checkboxes. Every task follows the same cycle: failing test → implement → passing test → update `docs/IMPLEMENTATION_STATUS.md` → commit. Each milestone gets step-level detail when it starts, so this plan stays shorter than the brief.

**Goal:** Build the MotionMirror mono-repo from the brief, so the whole application runs on Mac, Ubuntu and Windows with or without a GPU, and real AI output runs on a CUDA GPU server.

**Architecture:** Next.js is the UI. A Spring Boot modular monolith is the system of record, backed by PostgreSQL with a transactional outbox. RabbitMQ dispatches work to Python workers, which act only under a fenced lease from the API. Redis caches and rate-limits but is never authoritative. Media lives in private S3-compatible storage.

**Tech stack (current releases checked 2026-11-06; exact versions and image digests are pinned in T1.1):** Java 21, Spring Boot 4.1.x (4.1.2), Node 26 LTS, Next.js 16.x, pnpm, Python 3.12 with uv, Diffusers 0.42.1, PostgreSQL, RabbitMQ 4.x, Redis, Keycloak 26.8.x, SeaweedFS 4.x, Caddy.

**Spec:** `docs/product/full-person-video-replacement-development-prompt.md` (the brief) and `docs/product/MotionMirror_Product_Documentation.docx`.

---

## Context

The repo is empty apart from the two documents, with no commits. Your request adds to the brief in three ways: it must run on Mac, Ubuntu and Windows with or without a GPU, production is GPU-enabled, and training should use a suitable non-commercial open dataset.

### Decisions you made

| Decision | Your answer | What it means |
| --- | --- | --- |
| GPU access | No GPU yet | I build and test everything possible on this Mac. Real-output and real-training checks are marked `pending GPU validation`. |
| Data licence | Keep the brief's rule | TikTok data can only produce a labelled research adapter, which can never become the production default. Production uses the base model until you supply consented footage. |
| Training | Prepare now, run later | I download the data, build real samples and run smoke training. The real run stays one explicit command, and hardware and cost are your decision later. |

### What I verified before planning

| Finding | Source | Effect on the plan |
| --- | --- | --- |
| This Mac is an M1 Pro with **16 GB** of memory. The Wan transformer is **34.55 GB**, T5 11.36 GB and CLIP 4.77 GB, about 51 GB in total. | `sysctl`, HF file list | Unquantized real inference can't run here. |
| The smallest GGUF is Q2_K at 6.46 GB. FP8 reportedly fails on Metal, and an M1 Max with 64 GB reportedly took 82 min for 2 s of Wan 2.2 14B (not Animate). | HF, third-party blog (low trust) | I run one time-boxed Mac experiment. It may well fail, and that result gets recorded. |
| Diffusers 0.42.1 ships `WanAnimatePipeline` with `mode="replace"`, a LoRA mixin and single-file loading. Its tests build a tiny model. | Diffusers docs and source | This becomes the **inference runtime**. The real pipeline code can be contract-tested on CPU with no GPU. |
| API rules: mask white = generate, black = preserve. Height and width divisible by 16. `segment_frame_length` = 4N+1 (default 77). `prev_segment_conditioning_frames` = 4N+1 (1 or 5). `guidance_scale` defaults to 1.0, which disables CFG, so a negative prompt has no effect. | Diffusers docs | These go into presets and validation. |
| DiffSynth's script says verbatim: `# 1*80G GPU cannot train Wan2.2-Animate-14B LoRA` and `# We tested on 8*80G GPUs`. Its LoRA example passes only video, pose and face, with no inpaint or mask inputs. | DiffSynth script | Real training may need a costly multi-GPU rental. Replacement-mode training must be checked in the code, not assumed. |
| The official preprocessing guide says mask extraction is for **single-person videos only**. | Wan `UserGuider.md` | The product selects one dancer among several, so I build a selection-driven pipeline on SAM 2. |
| The Wan preprocessing detector, YOLOv10, is **AGPL-3.0**. ViTPose and SAM 2 are Apache-2.0. | Upstream LICENSE files | I use the SAM 2 mask's bounding box in place of YOLOv10 and avoid the AGPL weights. To be confirmed in T2.2. |
| The MinIO community repo is archived and its images stopped (per secondary sources). SeaweedFS's source enforces `content-length-range` on POST policies. | News sources, SeaweedFS source | **SeaweedFS replaces MinIO** locally, as the brief's "verified equivalent" clause allows. A test proves it in T1.4. |
| TikTok dataset: `CC BY-NC 4.0`, 53.3 GB, per the Kaggle API. A mirror of the project page says research use only. | Kaggle API, secondary mirror | Primary dataset. The registry records both statements and follows the stricter one. |
| UBC Fashion's official page states **no licence**. HumanVid's terms are gated. The HF dance datasets I checked (`dance_1723_video_10s`, `dance_2858_video`, `dance-videos`) show no licence or source in their metadata. | Primary pages | UBC and HumanVid are registered but blocked until reviewed. The HF sets are rejected. |

### What you'll have at the end

| Machine | Application | Mask and pose | Generation | Training |
| --- | --- | --- | --- | --- |
| This Mac (no NVIDIA) | Full stack in containers | Real, in a native worker where the spike proves it works | Mock, labelled. Real only through a remote GPU worker. | Data building and smoke runs |
| Ubuntu, no GPU | Full stack | Real on CPU, slow | Mock, or a remote GPU worker | CPU smoke |
| Windows, no GPU | Full stack in **WSL2** with Docker Desktop | Same as Ubuntu | Same as Ubuntu | CPU smoke |
| Ubuntu + NVIDIA | Full stack | Real on CUDA | **Real** | Real, if there's enough GPU memory |
| Windows + NVIDIA | Same, through WSL2 | Real on CUDA | Real, as a separately verified path | Same as Ubuntu + NVIDIA |
| Production | CPU host plus GPU worker | Real | **Real** | Separate job |

"Without a GPU" means the full application with real selection and masks, while generation is mock and clearly labelled. No machine without a GPU produces real replacement video, unless the Mac experiment proves otherwise. AMD and Intel GPUs count as CPU.

**What won't be proven at the end:** real output, GPU benchmarks, real LoRA training and the MVP acceptance gate stay `pending GPU validation`. Ubuntu, Windows and CI can't be run from this Mac, so they stay unverified until you run them or push the repo. There's no git remote, `gh` isn't installed and the GitHub connector failed to connect. Nothing paid is created.

### What I'll do on your machine

- Install JDK 21 with `brew install openjdk@21` (only 22 and 27 are arm64 here) and start OrbStack.
- Use your existing `~/.kaggle/kaggle.json` to download the TikTok dataset (53 GB) to a git-ignored `.data/` on the project volume (625 GB free).
- Download SAM 2 and pose checkpoints, plus about 20 GB for the Mac experiment. The full 51 GB model isn't downloaded here.
- **Git, since commits need your OK:** rename `master` to `main`, make one documents-only first commit, then commit per task on `feat/mvp-build`. No pushes.

---

## Global Constraints

- Source clip ≤ 30 seconds; real preview 3–5 seconds; one selected person per job; continuous single-shot clips, never tracked silently across cuts.
- Mock output is labelled `MOCK / NO AI REPLACEMENT` in the UI, manifest and frames. It can never satisfy an acceptance gate.
- `select_device()` fails fast and never silently moves a CUDA path to CPU or MPS.
- Keep these labels distinct everywhere: `mock`, `real`, `experimental`. Record `device_type`, framework versions and dtype in every manifest.
- Task envelope fields: `schema_version`, `event_id`, `job_id`, `dispatch_generation`, `task_type`, `backend_id`, `config_revision`, `created_at`, bounded trace context. Never bytes, secrets, signed URLs, full prompts or executable arguments.
- Prefetch 1 per GPU consumer; one real generation per GPU. Ack only after the outcome is durably recorded. Spring Boot owns retries.
- Python never touches the database or Redis. Redis is never the authority for jobs, authorization or idempotency. Sessions stay in PostgreSQL.
- `commercial_use_ok` defaults to `false`. Never invent licences, hashes or VRAM figures. Datasets and weights are never fetched during bootstrap, tests or API startup, and never committed.
- Adapters are safetensors only, hash-pinned and admin-registered. No user-supplied paths, URLs, modules or checkpoints.
- Not in the MVP: Celery, Kafka, service mesh, Kubernetes, billing, 4K, multi-person replacement. No celebrity or scraped fixtures.
- No paid infrastructure, paid training or external publishing without your authorization.

## Review Focus

These are the five cases most likely to hurt real users. Each has a test in the task that owns it.

1. **Phone clips with variable frame rate and rotation metadata** should keep correct duration, audio sync and click position (T1.8, T2.1).
2. **The dancer leaves the frame or is fully hidden** should report "target lost at frame N" for review, not silently track someone else (T2.2).
3. **A clip with hard cuts** should be rejected with a clear explanation or need reselection per shot (T3.2).
4. **No audio, or several audio streams,** should give a valid MP4 with a defined result, never a failed job (T3.1).
5. **A bad primary photo** (face close-up, EXIF rotation, oversized or disguised file) should warn or reject clearly and normalize orientation (T2.3).

---

## Shared interfaces

Tasks in several languages depend on these, so they're fixed here.

```python
# services/ml-worker/src/motionmirror/device.py
def select_device(profile: Literal["cuda", "mps", "cpu"]) -> DeviceSelection: ...  # raises UnsupportedDeviceError

# services/ml-worker/src/motionmirror/backends/base.py  (all types fully defined in contracts/)
class VideoReplacementBackend(Protocol):
    def capabilities(self) -> BackendCapabilities: ...
    def validate(self, request: GenerationRequest) -> ValidationResult: ...
    def prepare(self, request: GenerationRequest) -> PreparedInputs: ...
    def generate(self, inputs: PreparedInputs, context: JobContext) -> GenerationResult: ...
    def release(self) -> None: ...
```

- **Job states:** `QUEUED, RUNNING, RETRY_WAIT, CANCEL_REQUESTED, SUCCEEDED, FAILED, CANCELLED`
- **Job kinds:** `PREPROCESS, PREVIEW, FULL`
- **Stages:** `validate, decode, segment, extract_motion, prepare_reference, generate, composite, encode, upload, verify`
- **Queues:** `motionmirror.preprocess.v1`, `motionmirror.preprocess.mock.v1`, `motionmirror.generate.wan-animate.v1`, `motionmirror.generate.mock.v1`, each with a dead-letter queue.
- **Acquire:** `POST /internal/v1/jobs/{id}/acquire {event_id, dispatch_generation, worker_id}` returns `200 {attempt_id, attempt_no, fencing_token, lease_expires_at, request}` or `409 {disposition: DUPLICATE|OBSOLETE|CANCELLED}`.
- **Heartbeat, progress, complete, fail:** all carry `{attempt_id, fencing_token}`. A stale token returns `409 LEASE_LOST`. The heartbeat response includes `cancel_requested`.
- **Python packaging:** `motionmirror` has a base install with **no torch**, plus extras `[preprocess]` and `[wan]`. `training/` has its own lockfile and depends on `motionmirror[preprocess]`, so training and serving share one preprocessing pipeline.

---

## Milestone 0: Foundations and feasibility spikes (Phase 0)

Results include negative ones and go into `docs/MODEL_CAPABILITIES.md`, `docs/MAC_APPLE_SILICON.md`, `docs/TRAINING.md`, `docs/DEPENDENCY_MATRIX.md` and `docs/BENCHMARKS.md`.

- [ ] **T0.1 Repo baseline.** Move both documents to `docs/product/`. Add `.gitignore` (media, weights, `.env*`, `.data/`, `.claude-flow/`, `.remember/`), `.gitattributes` (`* text=auto eol=lf`, for WSL2), `.dockerignore`, `README.md`, `docs/IMPLEMENTATION_STATUS.md` (with P0, P1, P2 labels) and `docs/architecture/decisions/` holding the ADRs from the table above. Copy this plan to `docs/superpowers/plans/2026-11-06-motionmirror-master-plan.md`. Commit documents only on `main`, then branch to `feat/mvp-build`.
- [ ] **T0.2 Diffusers contract spike.** Files: `services/ml-worker/tests/contract/test_wan_animate_tiny.py`. Build a tiny `WanAnimatePipeline` from config with no real weights and run it on CPU and MPS. Tests assert:
  - non-16-divisible sizes raise an error
  - non-4N+1 segments are handled as documented
  - total output frames equal the pose frames across several segments
  - white mask regions change and black regions stay nearer the source
  - a negative prompt changes nothing at `guidance_scale=1.0` and does at `>1`
- [ ] **T0.3 Apple Silicon spike.** Files: `services/ml-worker/src/motionmirror/cli/spike_mac.py`. Record time and peak memory for: bf16 and float64 on MPS, ops that need CPU fallback, SAM 2 on MPS vs CPU, and ViTPose ONNX on CPU vs CoreML. Then try real Animate from `Q2_K` GGUF at the smallest valid size with a **2-hour limit and a memory watchdog that aborts on sustained pressure**, so the laptop isn't left thrashing. A failure counts as a valid result.
- [ ] **T0.4 CUDA CLI spike, code only.** Files: `services/ml-worker/src/motionmirror/cli/spike_infer.py`. Takes a clip and a primary reference, writes preprocessing artifacts and attempts a replacement, then writes a benchmark manifest. Test it end to end with the tiny pipeline. The real run stays `pending GPU validation`.
- [ ] **T0.5 Training-path spike.** Pin a DiffSynth-Studio commit and read `train.py` at that commit to settle:
  - whether `animate_inpaint_video` and `animate_mask_video` actually train
  - whether `input_image` can come from a different frame, not `video[0]`
  - the `metadata.csv` format and device assumptions

  Test: a safetensors file with DiffSynth's key names loads through `load_lora_weights` on the tiny pipeline and changes the output. If it doesn't, write and test a converter, or record the exact gap.
- [ ] **T0.6 Dataset spike.** Fetch the TikTok dataset, check its real layout and checksum it, then build a few samples using the pipeline from T4.1, which is built first for this purpose. Record the actual file structure and any terms found in the download.

## Milestone 1: Local foundation (Phase 1)

Exit: the authenticated mock journey works through real RabbitMQ and Redis on this Mac and survives a restart. Duplicate-delivery tests pass, and services run natively on arm64.

- [ ] **T1.1 Mono-repo and Compose.** `pnpm-workspace.yaml`, `Makefile`, `scripts/doctor.py`, `infra/compose/compose.yaml` with profiles `lite`, `full`, `gpu`. Pin per-platform digests and set `platform` explicitly, using named volumes for data. Bind admin ports to loopback. `make doctor` reports chip, memory, disk, runtime, architecture, emulation, MPS, FFmpeg codecs, and on Linux the NVIDIA driver and GPU visibility, and it **fails on silent emulation**. Measure RAM and disk for `lite` and `full`. Test: `tests/test_doctor.py` with an emulation case.
- [ ] **T1.2 Contracts.** `packages/contracts/`: `messages/task-envelope.v1.schema.json`, AsyncAPI, `generation-settings.v1.schema.json`, generated OpenAPI TS client. Shared `fixtures/valid` and `fixtures/invalid` are run by both JUnit and pytest, so both languages accept and reject the same inputs, including unknown fields and versions.
- [ ] **T1.3 API, schema and identity.** `apps/api` packages `com.motionmirror.{identity,projects,assets,selections,jobs,presets,models,workers,audit}`. Flyway `V1`–`V5` covering all tables in brief §9.
  - Browser chain: OAuth2 Login with PKCE and Spring Session JDBC.
  - Worker chain: Resource Server validating issuer, audience, algorithm, expiry and scopes.
  - Docker hostnames: the browser uses the public issuer, the API uses internal token and JWKS endpoints, and the **issuer check stays on**.
  - `infra/identity/realm-template.json` holds no secrets.
  - Tests: invalid issuer, audience, expiry and scope; cookie flags; CSRF and CORS failures; guessed-UUID and cross-project access.
- [ ] **T1.4 Projects and assets.** Upload intents use a presigned **POST policy** with `content-length-range`, using a small SigV4 policy signer if the AWS SDK doesn't provide POST policies. Assets stay `QUARANTINED` until validated, then copy to an immutable server-only key, so a still-valid upload URL can't replace an inspected file. Tests run on SeaweedFS: oversize rejected, finalized-object overwrite blocked, wrong type rejected, signed-download authorization. If SeaweedFS fails, try the `pgsty/minio` fork and record the result.
- [ ] **T1.5 Jobs, outbox, topology.** Job and event commit in one transaction. The publisher reserves rows with `FOR UPDATE SKIP LOCKED`, publishes with no lock held, and uses confirms and mandatory returns. It marks an event published only after confirmed routing. `configs/rabbitmq/` holds topology, scoped identities and quorum queues. Testcontainers tests: transaction rollback, publish-before-mark crash, broker outage and reconnect, mandatory return, poison message to DLQ, negative permission checks. Write `docs/MESSAGING.md`.
- [ ] **T1.6 Leases and reconciler.** All `/internal/v1/**` endpoints, with atomic acquire and a monotonic fencing token. The reconciler is coordinated by advisory lock. Tests: concurrent acquire gives one winner, a stale worker gets `LEASE_LOST`, completion-before-ack, a cancel/complete race ends in a single state, a retry limit, and a duplicate delivery that doesn't execute.
- [ ] **T1.7 Redis and quotas.** Cache-aside with TTL, jitter and versioned keys. Atomic limiter script. Hard per-user concurrency in PostgreSQL. Tests: cross-user isolation, invalidation after delete, stampede limit, `noeviction` errors. With Redis down, new expensive work gets a retriable error while heartbeat, cancel and results keep working. Write `docs/REDIS.md`.
- [ ] **T1.8 Worker and mock backend.** `services/ml-worker/src/motionmirror/{worker,media,backends}`. aio-pika consumer, lease client, heartbeat, attempt run in an isolated process with a terminate timeout. FFmpeg uses argument arrays and `-protocol_whitelist file,pipe`. Tests:
  - rational timestamps
  - variable frame rate and rotation (generated fixtures)
  - mock output has the visible label and the importing process has no `torch` in `sys.modules`
  - a long mock attempt crosses the normal consumer-timeout boundary under the configured deadline
- [ ] **T1.9 Web journey.** `apps/web`: login, projects, upload with progress, job status polling with backoff, result download, `no-store`, CSP. Playwright: create project → upload → queue mock → real progress → download visibly mocked output → reload; plus a restart check for persistence.

## Milestone 2: Selection, real preprocessing, real backend (Phase 2)

Exit: real masks and poses from a real clip on this Mac, and a backend proven on the tiny pipeline. The short real preview stays `pending GPU validation`.

- [ ] **T2.1 Selection.** `apps/web/src/features/selection/coords.ts`: `displayToSource`, `sourceToModel`. Canvas with points, box and paint. Selections stored as revisions with frame index and timestamp. Tests: letterbox and rotation mapping round-trips, keyboard use, ownership.
- [ ] **T2.2 Real preprocessing.** `services/ml-worker/src/motionmirror/preprocessing/`. SAM 2 seeded by the user's prompts; mask-derived box; ViTPose-H ONNX; face crops; Apache-2.0 Wan pose-rendering code vendored at a pinned commit with attribution; PyAV or FFmpeg instead of `decord`. Output matches `src_pose`, `src_face`, `src_bg`, `src_mask`. Cache by source checksum, selection revision, config and tool revision. Tests: mask polarity and resizing, target-lost reporting, cache hit when only the photo changes, and output compared with the official script on a single-person clip. Real-weight tests are marked `requires_weights`.
- [ ] **T2.3 References and preview UI.** Gallery with view labels and one primary image; EXIF stripped and orientation normalized; pixel limits; raster only. Warn when the primary looks like a close-up. Mask and pose preview with corrections that create a new revision. UI states exactly which photo was used.
- [ ] **T2.4 `WanAnimateReplacementBackend`.** `backends/wan_animate.py`, `backends/registry.py`, `configs/presets/{preview,standard}.v1.yaml`, `configs/models/wan-animate-14b.yaml`. Presets use valid values from the verified API. Validation rejects a negative prompt with `guidance_scale ≤ 1` (`NEGATIVE_PROMPT_REQUIRES_GUIDANCE`) in both Java and Python. Requested and effective settings are both persisted. OOM is its own failure category with no silent quality drop. Tests run on the tiny pipeline.
- [ ] **T2.5 GPU path.** `services/ml-worker/Dockerfile.cuda`, `make build-worker-amd64` plus an architecture check, `make models-download` (resumable, pinned, disk-checked, with a manifest), `make smoke-gpu`, `make benchmark-gpu`, `make up-gpu`, `make worker-native-mac`. Remote-worker connectivity and authorization tests from brief §13C.

## Milestone 3: Full MVP (Phase 3)

Exit: a 30-second clip completes end to end on the mock and tiny backends, and recovery tests pass. The real 30-second run stays `pending GPU validation`.

- [ ] **T3.1 Long clips.** Segment and boundary-frame accounting, soft-edge compositing that doesn't clip hair or limbs, no unconditional crossfades, aligned audio, broadly playable MP4, probe before success. Tests: duration within one output frame, silent input, multi-stream audio, variable frame rate, cleanup.
- [ ] **T3.2 Job control.** Trim, shot detection with a clear rejection, cancel, retry, history, events, failure explanations. `make test-failure-recovery`: worker kill, API restart, expired lease, expired signed URL, stale message. Real stages and counts only, with no invented percentages or ETAs.
- [ ] **T3.3 Lifecycle.** Consent confirmation, AI-edited labelling, deletion that revokes access, fences work, purges caches and removes all derived data, audit events, tombstones. Tests cover each.

## Milestone 4: Training track and adapter deployment (Phases 5–6)

Exit: the licence gate demonstrably blocks data, real samples are built with person-level splits, `cpu-smoke` and `mac-mps` results are recorded, and adapter promotion and rollback work through the API. The real LoRA run and the evaluation gate stay `pending GPU validation`.

- [ ] **T4.1 Datasets.** `configs/datasets/registry.yaml`, `training/src/motionmirror_training/datasets/`. Entries:
  - `tiktok-dance`: reviewable, `commercial_use_ok: false`, research adapters only
  - `ubc-fashion`: `license: unstated`, blocked
  - `humanvid-pexels`: terms unverified, blocked
  - rejected HF sets, with reasons

  `make datasets-fetch` and `make datasets-validate` are resumable, disk-checked and checksummed. Pipeline: clip filter with recorded rejection reasons, shared preprocessing, reference from a **different frame**, manifest, person and clip-level splits, near-duplicate detection, fixed evaluation set, dataset card. Tests: gate blocks unreviewed data, split leakage fails, no fetch during tests. Write `docs/DATASETS.md`.
- [ ] **T4.2 Training.** `configs/training/profiles/{gpu-cuda,mac-mps,cpu-smoke}.yaml` share one schema.
  - `gpu-cuda` wraps the pinned DiffSynth trainer in its own image.
  - `cpu-smoke` and `mac-mps` run a small real PEFT loop on the tiny model, labelled smoke, proving data loading, an optimizer step, checkpoint, resume and safetensors export. It can't satisfy the training gate.
  - `make train-lora` refuses to start without a confirmation flag, a duration estimate and disk and memory checks.

  Every run writes a manifest. The Mac preflight records why real 14B training doesn't fit, and may measure one shard rather than attempt a full load that would swap heavily. Write `docs/TRAINING.md`.
- [ ] **T4.3 Evaluation.** `make eval-adapter` runs base vs adapter on the fixed set and seeds through the real worker path, and writes `eval_manifest.json`, a contact sheet and a model card. It refuses non-CUDA hardware for promotion evidence.
- [ ] **T4.4 Adapter registry.** Admin endpoints, immutable preset revisions, worker-scoped artifact access, hash check and capability check before load. **Research-labelled adapters can't be promoted**, per your licence decision. Tests: base-revision mismatch, hash mismatch, missing evaluation, unreviewed licence, non-admin caller, path or URL rejected, in-flight snapshot kept, rollback converges with stale caches. Write `docs/MODEL_DEPLOYMENT.md`, `docs/MODEL_CUSTOMIZATION.md`.

## Milestone 5: Deployment, hardening, handoff (Phase 4)

Exit: runbooks and tooling are complete and run locally where possible. Nothing is deployed to paid infrastructure.

- [ ] **T5.1 Deployment artifacts.** Hardened images (non-root, dropped capabilities, read-only where supported), production Compose and Caddy, startup checks that reject insecure defaults, mock mode in deployment and incompatible broker deadlines. `docs/runbooks/CLOUD_GPU_DEPLOYMENT.md` for Runpod plus a CPU host, naming one concrete managed broker and cache after checking their policies, with a sizing worksheet, cost worksheet and exact teardown. Each step is marked `provided`, `locally validated` or `deployed/tested`.
- [ ] **T5.2 Observability.** OpenTelemetry across HTTP and AMQP, Micrometer, `make observability-up`, checked-in Grafana dashboards and alerts. Tests that prompts, tokens and IDs stay out of logs and metric labels. Readiness separated from liveness. Write `docs/OBSERVABILITY.md`.
- [ ] **T5.3 Security and CI.** `docs/SECURITY.md` threat model; Gitleaks, Semgrep, Trivy, CycloneDX, Cosign; ClamAV profile; bounded k6 test. `.github/workflows/`: `ci.yml` (Linux full suite; macOS and Windows jobs for what those runners can actually run), `release.yml`, and `gpu-acceptance.yml` as manual with explicit spend authorization. Actions pinned by SHA. Run the scanners locally and fix or formally except each high or critical finding.
- [ ] **T5.4 Documentation and handoff.** Finish every document listed in brief §18, with commands re-run against the real scripts. Windows gets its own guide in `docs/LOCAL_DEVELOPMENT.md`: repo inside the WSL filesystem, Docker Desktop integration, and GPU passthrough marked as a separately verified path. Run `/code-review` over the branch. Final report: what was built, commands run and their results, limitations, whether GPU or cloud validation happened (it won't have).

---

## Verification

Run on this Mac, with evidence recorded in `docs/IMPLEMENTATION_STATUS.md`:

```bash
make doctor                    # arm64 native, no emulation, MPS and codecs reported
make bootstrap && make up-mac  # full stack healthy
make test                      # unit and contract tests, including the tiny Wan pipeline
make test-integration          # real PostgreSQL, RabbitMQ, Redis via Testcontainers
make test-failure-recovery     # kill, restart, expired lease, duplicate delivery
make test-security             # authorization, upload abuse, scanners
make test-e2e                  # Playwright mock journey, including restart persistence
make datasets-validate DATASET=tiktok-dance
make train-smoke PROFILE=cpu-smoke && make train-smoke PROFILE=mac-mps
make down                      # keeps data; a separately named target does the destructive reset
```

I'll also walk the journey in a real browser: log in, upload a clip, select a dancer, see a real mask preview, run a mock job and download the labelled output.

| Acceptance gate | Expected status at handoff |
| --- | --- |
| Local foundation (Phase 1 exit) | Met on this Mac, with evidence |
| MVP gate (§16) | `pending GPU validation`; every CPU-testable item evidenced |
| Training gate (§16) | `pending GPU validation`; data, licence gate, smoke runs and adapter API evidenced |
| Ubuntu, Windows/WSL2, CI | Written but unverified until run |

**To finish later on a GPU:** `make up-gpu` → `make smoke-gpu` → `make benchmark-gpu`, then the 30-second acceptance run; and `make train-lora PROFILE=gpu-cuda CONFIG=<file> CONFIRM=yes` once you've decided on hardware and budget.
