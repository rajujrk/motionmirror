# Testing

| Command | What runs | Needs |
| --- | --- | --- |
| `make test` | Unit and contract tests, including the real Wan pipeline code on tiny weights | Nothing downloaded, no containers |
| `make test-integration` | Real PostgreSQL, RabbitMQ, Redis and storage | Docker |
| `make test-e2e` | Browser journeys, including real sign-in | `make up-mock` first |
| `make test-failure-recovery` | A killed worker, an API restart, a frozen worker, a storage outage | Docker. Takes several minutes, and restarts the stack |
| `make test-security` | Scanners, plus authorization and upload abuse | Docker and network for scanner rules |
| `make test-tools` | Scripts, CI and production configuration | Nothing |
| `make test-load` | Bounded k6 run on mock jobs | A signed-in session, see the Makefile |

## Real weights

Skipped unless set up, and never downloaded by a test.

```bash
MM_DEVICE_PROFILE=mps MM_TEST_CLIP_DIR=<folder with video.mp4 and dataset_mask.mp4> \
  uv run --project services/ml-worker --extra wan --extra preprocess --group dev \
  pytest services/ml-worker/tests/contract/test_real_preprocess.py

# Real tracking through the whole system, with make up-mac-native and make worker-native-mac running:
cd tests/e2e && MM_E2E_REAL_CLIP=<clip of one person> pnpm exec playwright test native
```

## Fixtures

Generated on demand and synthetic, so no real person's likeness is involved. Real clips stay under the git-ignored `.data/`.

## What "tiny weights" proves

Frame accounting, segment joins, audio, compositing, mask polarity, offload, adapter loading and failure handling all run through the real pipeline code. Nothing about quality. A guard test keeps the tiny model's layers matching the real architecture.

## Acceptance checklist for a real run

`pending GPU validation`. On a production-class CUDA worker, for a 30-second clip:

- [ ] `make smoke-gpu` passes, and a short preview looks right.
- [ ] The output's length is within one frame of the selection, and audio stays in sync.
- [ ] The person's **likeness** holds, with **outfit continuity** across the clip.
- [ ] **Motion** matches the source, including **spins and back views**.
- [ ] **Hands** and **feet** are acceptable, and feet stay planted.
- [ ] **Occlusion** is handled, with no **flicker**, and **mask edges** don't clip hair or limbs.
- [ ] Everything outside the edit region is unchanged, and any other dancer is unaffected.
- [ ] The manifest records device, versions, settings and consumed references, and holds no prompts.
- [ ] Peak GPU memory and timings are written to `docs/BENCHMARKS.md`.

## Where the evidence is

`docs/IMPLEMENTATION_STATUS.md` holds results by task, and `docs/BENCHMARKS.md` holds measurements. Run outputs go under the git-ignored `.data/runs/`.
