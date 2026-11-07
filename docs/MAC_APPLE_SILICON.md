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

## Image rules

Every service image is a multi-architecture index pinned by digest, and `platform` comes from `.env`, so a wrong architecture fails visibly. `make doctor` fails on silent emulation. The CUDA worker image is `linux/amd64` only. Build it with `make build-worker-amd64` and check the architecture before starting a GPU host.

## Dtypes

`float16`, `bfloat16` and `float32` work on MPS here. `float64` raises a `TypeError`. No SAM 2 operator needed the CPU fallback, so `PYTORCH_ENABLE_MPS_FALLBACK` stays unset.

## Limits and measurements

See `docs/BENCHMARKS.md`. In short, real masks and poses take about 1.9 s per frame, so a 30-second clip takes about half an hour. Holding a 27 s clip in memory drove swap up by 6.5 GB and was stopped, so tracking now runs in chunks and writing streams, and that clip finishes.

Close other heavy apps for long clips. `spike_infer --watchdog` stops a run at 6 GB of swap growth or sustained critical pressure, so it can't leave the laptop unusable.

## Remote worker

The recommended way to get real output. See `docs/LOCAL_DEVELOPMENT.md` and `docs/runbooks/CLOUD_GPU_DEPLOYMENT.md`.
