# MotionMirror (provisional name)

A private video-editing application. You upload a dance clip, select one dancer and add photos of yourself, and the application produces a video in which your appearance replaces that dancer while keeping the original performance. Results are labelled AI-edited. Likeness, anatomy and foot contact are quality targets, not guarantees.

## What works today

| Capability | Status |
| --- | --- |
| The whole application: sign-in, projects, uploads, marking a dancer, jobs, results, deletion | **Works**, with browser tests through real services |
| Recovery from a killed worker, an API restart, a frozen worker and a storage outage | **Works**, tested by breaking the running stack |
| Real masks and poses from a real clip | **Works on a Mac**, labelled experimental, up to a 27-second clip measured |
| Generation on a machine with no NVIDIA GPU | **Mock only**, labelled `MOCK / NO AI REPLACEMENT` in the UI, the manifest and every frame |
| Real video replacement | **Built, never run: `pending GPU validation`.** Tested on tiny random weights, which prove the code's behaviour and nothing about quality |
| Real LoRA training | **Built, never run: `pending GPU validation`.** The trainer's own script says one 80 GB GPU isn't enough |
| Ubuntu, Windows through WSL2, and CI | **Written, not yet run** |

No machine without an NVIDIA GPU produces real replacement video. See [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md) for evidence and what's left.

## Quick start

Needs Docker, Python 3, `uv`, Node 26 with pnpm, and Java 21. `make doctor` checks your machine and changes nothing.

```bash
make bootstrap    # Creates .env with generated secrets. Downloads no models or data
make up-mock      # The full stack with the mock worker
```

Open <http://localhost:8088> and sign in as `dev.user`, with `MM_DEV_USER_PASSWORD` from `.env`. `make down` stops everything and keeps your data.

**Real masks and poses on a Mac:**

```bash
make models-download PROFILE=preprocess    # About 2.7 GB
make up-mac-native
make worker-check
make worker-native-mac
```

**Real generation on an NVIDIA machine:** see [`docs/LOCAL_DEVELOPMENT.md`](docs/LOCAL_DEVELOPMENT.md), and for a rented GPU, [`docs/runbooks/CLOUD_GPU_DEPLOYMENT.md`](docs/runbooks/CLOUD_GPU_DEPLOYMENT.md). Validate the GPU with `make smoke-gpu` before downloading anything.

`make help` lists every command.

## Repository

| Path | What's there |
| --- | --- |
| `apps/api` | Spring Boot API, the system of record |
| `apps/web` | Next.js UI |
| `services/ml-worker` | Python worker: media, preprocessing, the backends |
| `training` | Dataset tools, samples, training, evaluation, adapter commands |
| `packages/contracts` | Message and settings schemas, used by both languages |
| `configs`, `infra` | Presets, models, broker, Compose, proxy, observability |
| `tests/e2e`, `tests/tools`, `tests/load` | Browser journeys, script and configuration checks, load test |

## Documentation

| Start here | |
| --- | --- |
| [Implementation status](docs/IMPLEMENTATION_STATUS.md) | What's done, the evidence, blockers, what's next |
| [Architecture](docs/architecture/OVERVIEW.md) | How the pieces fit, and why |
| [Local development](docs/LOCAL_DEVELOPMENT.md) | Every way to run it, including Windows |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Symptoms and fixes |

| Reference | |
| --- | --- |
| Running it | [Prerequisites](docs/PREREQUISITES.md), [Stack](docs/STACK.md), [Dependency matrix](docs/DEPENDENCY_MATRIX.md), [Apple Silicon](docs/MAC_APPLE_SILICON.md), [Benchmarks](docs/BENCHMARKS.md) |
| Models | [Capabilities](docs/MODEL_CAPABILITIES.md), [Setup](docs/MODEL_SETUP.md), [Customization](docs/MODEL_CUSTOMIZATION.md), [Deployment](docs/MODEL_DEPLOYMENT.md) |
| Training | [Datasets](docs/DATASETS.md), [Training](docs/TRAINING.md) |
| Internals | [API](docs/API.md), [Messaging](docs/MESSAGING.md), [Redis](docs/REDIS.md) |
| Operating it | [Security](docs/SECURITY.md), [Observability](docs/OBSERVABILITY.md), [CI and releases](docs/CI_CD.md), [Testing](docs/TESTING.md), [Operations](docs/runbooks/OPERATIONS.md), [Cloud GPU deployment](docs/runbooks/CLOUD_GPU_DEPLOYMENT.md) |
| Background | [Product documents](docs/product/), [Plan](docs/superpowers/plans/), [Decisions](docs/architecture/decisions/) |
