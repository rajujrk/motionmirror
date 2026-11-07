# Model setup

Weights are never fetched during bootstrap, tests or startup, and they are never committed. Downloads are explicit, pinned to a commit, resumable and checked against disk space first, and every file is hashed against the hub's records.

```bash
make models-download PROFILE=preprocess       # SAM 2.1 small and ViTPose. About 2.7 GB
make models-download PROFILE=preprocess-gpu   # SAM 2.1 large and ViTPose
make models-download PROFILE=wan              # Everything real generation needs. About 83 GB on disk
make models-download PROFILE=mac-spike        # The Mac experiment: everything except the full transformer, plus the Q2_K GGUF
make models-download PROFILE=training         # The trainer's checkpoint, including its .pth pickles
```

Files land under `MM_MODEL_CACHE` (default `.models/`), and `manifest.json` there records each file's hash, revision and licence.

| Artifact | Repository | Revision | Licence |
| --- | --- | --- | --- |
| Wan-Animate (Diffusers) | `Wan-AI/Wan2.2-Animate-14B-Diffusers` | `6f4df10861c758af86ac3c979aacc1bf5c03eff0` | Apache-2.0 |
| Wan-Animate (original, training and pose) | `Wan-AI/Wan2.2-Animate-14B` | `cb93a225fbaf1ca100f54e79da8f994995b689b3` | Apache-2.0 |
| SAM 2.1 small | `facebook/sam2.1-hiera-small` | `ee5bba1d82bb8749febdf90f45e84b687142ba03` | Apache-2.0 |
| SAM 2.1 large | `facebook/sam2.1-hiera-large` | `665f8e2ad61cf5f53d65644ff27c8ee525124610` | Apache-2.0 |
| Q2_K GGUF, Mac experiment only | `QuantStack/Wan2.2-Animate-14B-GGUF` | `33c51bb84d4e70ffc0d088aeb6068d40d9446fa3` | Apache-2.0, as a conversion |

YOLOv10 from the official preprocessing is AGPL-3.0 and isn't used. The tracked mask gives the box instead. FLUX Kontext is non-commercial and isn't used.

## Check it worked

```bash
make models-download PROFILE=preprocess    # A second run downloads nothing and hashes everything again
MM_DEVICE_PROFILE=mps MM_TEST_CLIP_DIR=<folder with video.mp4 and dataset_mask.mp4> \
  uv run --project services/ml-worker --extra wan --extra preprocess --group dev pytest services/ml-worker/tests/contract/test_real_preprocess.py
```

A missing file fails the job with `MODEL_MISSING` and names the command to run. The CUDA image sets `HF_HUB_OFFLINE=1`, so it can't quietly download at run time.

## On a GPU host

Mount the cache read-only at `/models`. The image reads `configs/models` from inside itself, so downloading on the host needs only `uv`, or run the image's module with the cache mounted read-write once.
