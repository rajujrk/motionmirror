# Dependency matrix

Pinned versions per tier, from `services/ml-worker/uv.lock` and `training/uv.lock`. Python lockfiles are universal, so each platform gets the same versions.

## Models

See the table in `docs/MODEL_SETUP.md`. Trainer: DiffSynth-Studio at `08fdc0da8f41426885d9432e9007e6e460a4c30d`, Apache-2.0.

## Python

| Package | Version | CUDA | MPS | CPU |
| --- | --- | --- | --- | --- |
| torch | 2.15.0 | Yes | Yes | Yes |
| torchvision | 0.29.2 | Yes | Yes | Yes |
| diffusers | 0.42.1 | Yes | Tiny model and spike only | Tiny model only |
| transformers | 5.22.2 | Yes | Yes | Yes |
| accelerate | 1.15.0 | Yes | Yes | Yes |
| peft | 0.21.2 | Yes | Yes | Yes |
| safetensors | 0.8.0 | Yes | Yes | Yes |
| onnxruntime-gpu | 1.31.1 | Yes, Linux x86_64 | | |
| onnxruntime | 1.31.1 | | Yes | Yes |
| opencv-python-headless | 4.14.0.94 | Yes | Yes | Yes |
| huggingface-hub | 1.33.0 | Yes | Yes | Yes |
| gguf | 0.19.0 | | Mac experiment | |
| deepspeed (training image) | 0.19.9 | Linux only | | |

## What each tier can do

| Tier | Machine | Tracking and pose | Generation | Training | Status |
| --- | --- | --- | --- | --- | --- |
| `cuda` | Linux x86_64 with an NVIDIA GPU | Real | Real | Real, if there's enough GPU memory | `pending GPU validation` |
| `mps` | Apple Silicon | Real, labelled experimental | Mock, or a remote worker | Smoke only | Measured on an M1 Pro with 16 GB, see `docs/BENCHMARKS.md` |
| `coreml` | Apple Silicon | Pose only, with no measured gain over CPU (0.82 s against 0.82 s per frame), so unused | | | Measured |
| `cpu` | Anything | Real, slow | Mock | Smoke only | Measured on an M1 Pro. Linux and Windows are unverified |

AMD and Intel GPUs count as CPU. There are no CUDA-only packages in the Mac install.

## Dtypes on MPS, measured

`float16`, `bfloat16` and `float32` work. `float64` fails with `TypeError: Cannot convert a MPS Tensor to float64 dtype`. SAM 2 tracking ran with `PYTORCH_ENABLE_MPS_FALLBACK` unset, so it needed no CPU fallback, which would otherwise have raised an error.
