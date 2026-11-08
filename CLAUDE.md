# MotionMirror: notes for Claude Code

MotionMirror replaces one selected dancer in a short clip with a person from reference photos, using Wan2.2-Animate-14B.
This is a mono-repo: `apps/api` (Spring Boot), `apps/web` (Next.js), `services/ml-worker` (Python), `training/`, `infra/`.

Read first: `docs/IMPLEMENTATION_STATUS.md` (what's done, evidence, blockers), then the plan in
`docs/superpowers/plans/2026-11-06-motionmirror-master-plan.md` and the brief in `docs/product/`.

## Owner decisions

- Until 2026-11-08 no NVIDIA GPU had been used. Real generation, GPU benchmarks, real LoRA training and the MVP and
  training gates are `pending GPU validation`. Never claim them done without a recorded run on CUDA.
- TikTok dataset (CC BY-NC 4.0, research only): it can only ever produce a labelled research adapter, never the
  production default.
- Real training waits for the owner's hardware and cost decision. `make train-lora` needs their explicit go-ahead
  every time. Its authors note `# 1*80G GPU cannot train Wan2.2-Animate-14B LoRA` and `# We tested on 8*80G GPUs`.
- The repository is public at `https://github.com/rajujrk/motionmirror`. Pushing to `main` runs CI. Ask before each push.
- No paid infrastructure, paid training or external publishing without the owner's authorization.

## Rules that hold on every machine

- Labels stay distinct: `mock` (no AI), `experimental` (MPS or CPU), `real` (only from a run that recorded
  `device_type=cuda`). Mock output carries `MOCK / NO AI REPLACEMENT`.
- Record measured results only. Never invent licences, hashes or GPU memory figures. A failure, such as an
  out-of-memory error, is a valid result: record it with its settings.
- Datasets and weights are never fetched during bootstrap, tests or startup, and never committed.
- Adapters are safetensors only, hash-pinned and registered by an admin. No user-supplied paths, URLs or checkpoints.
- Python never touches the database or Redis. Task messages carry identifiers only. Prompts, tokens and signed URLs
  stay out of logs and manifests.
- **TikTok data:** never open raw frames with image-viewing tools; check them numerically or view derived skeleton and
  mask images only. Its licence says it "will not be transferred to outside parties" without the authors' permission,
  so don't copy it to a machine someone else operates without that. GPU validation doesn't need it: use a clip and
  photo the owner has the rights to.
- Write tests first, gate commits on passing tests, stage explicit paths, and end commit messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Use they/them for people.

## On a CUDA machine: validating real output

This is the next milestone. Linux with an NVIDIA GPU is the supported path. Windows through WSL2 is written but unverified.

1. Needs the NVIDIA driver, the NVIDIA Container Toolkit, Docker with Compose, Python 3 and `uv`. Disk: about 83 GB for
   generation weights and 2.7 GB for preprocessing, plus images. See `docs/PREREQUISITES.md`.
2. `make doctor`. It must show the GPU and no emulation.
3. `make bootstrap`. Creates `.env` with generated secrets. Downloads nothing.
4. `make models-download PROFILE=preprocess-gpu`, then `make models-download PROFILE=wan`. Resumable, pinned and
   disk-checked. Weights land in `.models/`, which the GPU worker mounts read-only at `/models`.
5. `make up-gpu`, then `make smoke-gpu`. Without arguments that's fitness and connectivity only.
6. Put a short clip and a photo in `.data/benchmark/`, which the worker sees as `/media`. Then a short real preview:
   `make smoke-gpu CLIP=/media/clip.mp4 PHOTO=/media/photo.png MARK=0.5,0.5`. `MARK` is `x,y` from 0 to 1 on the
   dancer.
7. `make benchmark-gpu CLIP=/media/clip.mp4 PHOTO=/media/photo.png MARK=0.5,0.5 PRESET=standard`. Results go to
   `.data/benchmark/runs/`.
8. Then the 30-second acceptance run through the browser, as in the brief's MVP gate.
9. Record what was measured, with the date, GPU model, driver, dtype (`MM_WAN_DTYPE`) and offload (`MM_WAN_OFFLOAD`):
   `docs/BENCHMARKS.md` (GPU memory, time, cold and warm start), `docs/MODEL_CAPABILITIES.md` and
   `docs/IMPLEMENTATION_STATUS.md`. Only then change `pending GPU validation` for what was actually run.

Also worth running there, since none of it has run on Linux yet: `make test`, `make test-integration`, `make up-mock`
then `make test-e2e`, `make test-failure-recovery`, and `make build-worker-amd64`. Check the GitHub Actions results too.

## Known risks to keep in view

- The edit region is a rectangle. On a measured clip it covered 53% of the frame against the dancer's 32%, so
  background inside it is regenerated. Judge this on real output.
- The trainer's frame pairing is inferred from its code and must be checked on the first real training run.
- Splits are by compilation, not by person, because the dataset has no person ids.
- Diffusers 0.42.1 can't take `image_embeds` in `WanAnimatePipeline`. A contract test pins it.

## Only on the owner's Mac (Apple M1 Pro, 16 GB)

- It also runs the owner's native PostgreSQL, Redis, RabbitMQ and a Java app on port 8080, and the OrbStack container
  `festive_mclaren` belonging to a plugin. Don't stop or change them, and don't stop OrbStack. `make down` keeps volumes.
- A GateGuard hook there asks for stated facts before the first shell command of a session and before
  destructive-looking ones. State them, then retry the identical command.
- Real generation there is impractical: one toy-size run took 475 s. See `docs/MAC_APPLE_SILICON.md`.
