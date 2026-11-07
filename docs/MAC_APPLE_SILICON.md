# Apple Silicon

Measured on an M1 Pro with 16 GB. A Mac is the primary development environment and isn't presented as equivalent to CUDA.

## What runs where

| Work | On this Mac | Label |
| --- | --- | --- |
| The whole application in containers | Native arm64, no emulation | |
| Upload validation | Container, or native in native mode | |
| Mock generation | Container, or native in native mode | `mock` |
| Real masks and poses | Native worker, up to a 27 s clip measured | `experimental` |
| Real generation | **Not practical.** See below. Use a remote CUDA worker | |
| Real training | **Not possible.** See `docs/TRAINING.md` | |
| Smoke training | Native | `smoke` |

## Why real generation doesn't fit

The transformer alone is 34.55 GB, against 16 GB of memory. The smallest GGUF is Q2_K at 6.46 GB, and T5 adds 11.36 GB.

### The one size that did run

Real Wan-Animate 14B from the Q2_K GGUF, on 2026-11-07, at the smallest valid size: 448x256, 9 frames, 4 steps, one segment, guidance 1.0. The stack was stopped. `make spike-mac CLIP_DIR=<clip> ONLY=gguf` reproduces it, after `make models-download PROFILE=mac-spike` for the weights (about 18 GB). The clip isn't used by this stage.

| Stage | Time | Memory |
| --- | --- | --- |
| T5 prompt encoding, bfloat16 on the CPU, then freed | 31.3 s | Peak RSS 5.78 GB |
| CLIP image encoding on the CPU, then freed | 5.5 s | |
| Q2_K transformer and VAE loaded to MPS | 50.1 s | MPS driver 7.32 GB, peak RSS 6.70 GB |
| 4 denoising steps | 338 s, about 85 s each | |
| Generation including the VAE decode | 380 s | MPS driver 10.97 GB |
| Whole run | 475 s | Swap grew about 3.1 GB. The watchdog didn't trip |

The output was 9 finite frames in range and smooth, close to the gray input. That shows the model ran, not that it replaces a person well: the inputs were blank. The first attempt failed on an input rule of the pipeline, now pinned by a contract test, after loading in similar times (35.4 s to encode, 55.7 s to load).

**What it means.** A preview at the preset's 432x768 with a 77-frame segment has about 25 times as many tokens. Attention grows faster than linearly with tokens, so each step would take at least about 25 times as long, roughly 35 minutes or more, and memory would far exceed 16 GB. That's an estimate from the token count, not a measurement. Real output on this Mac stays impractical, and a remote CUDA worker is the way to get it.

## Image rules

Every service image is a multi-architecture index pinned by digest, and `platform` comes from `.env`, so a wrong architecture fails visibly. `make doctor` fails on silent emulation. The CUDA worker image is `linux/amd64` only. Build it with `make build-worker-amd64` and check the architecture before starting a GPU host.

## Dtypes

`float16`, `bfloat16` and `float32` work on MPS here. `float64` raises a `TypeError`. No SAM 2 operator needed the CPU fallback, so `PYTORCH_ENABLE_MPS_FALLBACK` stays unset.

## Limits and measurements

See `docs/BENCHMARKS.md`. In short, real masks and poses take about 1.9 s per frame, so a 30-second clip takes about half an hour. Holding a 27 s clip in memory drove swap up by 6.5 GB and was stopped, so tracking now runs in chunks and writing streams, and that clip finishes.

Close other heavy apps for long clips. `spike_infer --watchdog` stops a run at 6 GB of swap growth or sustained critical pressure, so it can't leave the laptop unusable.

## Memory and disk per Compose profile

Measured 2026-11-07 with `docker stats`, the stack idle after about four hours. These are container figures. OrbStack's own VM overhead isn't included.

| Profile | Containers | Memory | Largest |
| --- | --- | --- | --- |
| `lite` | 10 | 1.77 GiB | Keycloak 841 MiB, API 483 MiB, SeaweedFS 150 MiB |
| `full` adds Prometheus, Tempo, Grafana | 13 | 1.95 GiB, just after start | Grafana 118 MiB. Prometheus grows with retained series |
| `security` adds ClamAV | +1 | About 970 MB more for clamd with its signatures loaded | |

Images whose sizes Docker reported: SeaweedFS 746 MB, worker 639 MB (shared by the mock and validation workers), API 313 MB, PostgreSQL 298 MB, web 238 MB, RabbitMQ 169 MB, Redis 119 MB. The digest-pinned Keycloak, Caddy, ClamAV and observability images aren't sized by OrbStack's image listing. All Docker images on this machine, including unrelated ones, take 7.98 GB. Volumes after these tests: PostgreSQL 83 MB, ClamAV signatures 176 MB, Grafana 533 MB, the rest under 12 MB each. Images, containers, volumes and build cache together took 23.3 GB by `docker system df`, including unrelated projects and 10.9 GB of build cache, of which `docker builder prune` can reclaim most. Uploaded media comes on top.

## Remote worker

The recommended way to get real output. See `docs/LOCAL_DEVELOPMENT.md` and `docs/runbooks/CLOUD_GPU_DEPLOYMENT.md`.
