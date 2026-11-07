# Training

Adapter fine-tuning of the pinned Wan-Animate checkpoint. This is neither foundation-model training nor per-user identity training. **No real LoRA run has been performed: `pending GPU validation`.**

## Profiles

All three share one schema, in `configs/training/profiles/`.

| Profile | Kind | Device | Can satisfy the training gate | What it proves |
| --- | --- | --- | --- | --- |
| `cpu-smoke` | Smoke, tiny random weights | CPU | **No** | The plumbing |
| `mac-mps` | Smoke, tiny random weights | MPS | **No** | The plumbing, on Apple Silicon |
| `gpu-cuda` | Real, the pinned trainer | CUDA | Only with the evaluation | The real thing, once run |

### Why real training doesn't fit on a Mac

The transformer alone is 34.55 GB, against 16 GB of memory, and the trainer's own script says one 80 GB GPU isn't enough. So the Mac runs the smoke loop only, and `make train-lora PROFILE=mac-mps` refuses.

## Commands

```bash
make samples-build DATASET=tiktok-dance CLIPS="00001 00029 00058"   # Real samples, one clip from each split
make train-smoke PROFILE=cpu-smoke                 # A real loop on tiny weights
make train-smoke PROFILE=mac-mps
```

Expected output is a `run_manifest.json` under `.data/runs/training/`, labelled `smoke`, with finite losses, `verification.layers_attached` above 0, and `verification.output_changed` true. Both the smoke loop and the real run train on the train split only, so validation and test clips stay unseen for evaluation. That last check loads the exported file into a fresh pipeline through the serving loader. Run the same command again with `--out` set to the same folder, and it resumes from the newest checkpoint.

### The real run

**This costs real money, and may need far more GPU than you'd expect.** The trainer's script says, verbatim, `# 1*80G GPU cannot train Wan2.2-Animate-14B LoRA` and `# We tested on 8*80G GPUs`.

```bash
make models-download PROFILE=training
make train-smoke PROFILE=gpu-cuda                  # A short real pass. Measures a step, and costs GPU time too
make train-lora PROFILE=gpu-cuda SECONDS_PER_STEP=<from calibration>                     # Shows the plan, starts nothing
make train-lora PROFILE=gpu-cuda SECONDS_PER_STEP=<...> CONFIRM=yes TRANSFER_PERMISSION=yes
```

Use `training/Dockerfile.cuda`, which holds the pinned trainer, and mount the cache at `/models` and the data at `/datasets`.

`train-lora` refuses, in this order, and each refusal names the fix:

1. The dataset's licence doesn't allow this use.
2. The licence limits transfer to outside parties and you haven't confirmed permission. A rented GPU may count as a transfer.
3. No CUDA GPU is visible. It never falls back.
4. This machine has less GPU than the authors tested, unless you pass `BELOW_TESTED=yes`.
5. Too little disk.
6. No duration estimate.
7. No confirmation. This one shows the plan: steps, estimated hours and GPU-hours.

A stopped run resumes from its newest checkpoint when started again with the same output folder. Hours multiply by your provider's rate, and the worksheet is in `docs/runbooks/CLOUD_GPU_DEPLOYMENT.md`.

## Run manifest

Every run writes hyperparameters, base model and revision, trainer repository and commit, targets and rank, dataset ids and versions, licence scope, seed, compute profile, measured peak memory, loss curves or their log files, export format and artifact hashes. A smoke manifest also says what it isn't: `can_satisfy_training_gate: false` and a `tiny-random` base model. A real run's GPU memory isn't recorded by the wrapper, so note it from `nvidia-smi`.

Checkpoints from the smoke loop are safetensors plus JSON, never pickles. The trainer's real checkpoint includes `.pth` pickles, which stay inside the training image.

## Measured

See `docs/BENCHMARKS.md`.

## Verified training path (T0.5)

Read from source at DiffSynth-Studio commit `08fdc0da8f41426885d9432e9007e6e460a4c30d` (2026-11-04, Apache-2.0). Nothing here has
been run at real scale.

| Question | Finding | Evidence |
| --- | --- | --- |
| Is there a LoRA path for Animate-14B? | Yes | `examples/wanvideo/model_training/lora/Wan2.2-Animate-14B.sh` |
| What hardware did the authors use? | The script says `# 1*80G GPU cannot train Wan2.2-Animate-14B LoRA` and `# We tested on 8*80G GPUs`, at 480×832, 81 frames | Same script |
| Distribution | `accelerate_config_14B.yaml`: DeepSpeed ZeRO stage 2, CPU offload, bf16. DeepSpeed makes this path Linux and CUDA in practice | Config file |
| Single-GPU options | `--enable_model_cpu_offload` and `--enable_optimizer_cpu_offload` exist, the first with an experimental split option. Unproven for Animate | `diffsynth/diffusion/parsers.py` |
| Do replacement inputs train? | The code path exists. `extra_inputs` passes any field through, and `WanVideoUnit_AnimateInpaint` builds `y` from `animate_inpaint_video`, `animate_mask_video` and `input_image`. The authors' example does **not** use it | `train.py`, `wan_video.py` |
| Mask polarity | `1 - mask`, so white = generate, matching Diffusers | `wan_video.py` |
| Control length | Pose, face, inpaint and mask videos are trimmed to `len(video) - 4` | `WanVideoUnit_AnimateVideoSplit` |
| Metadata | `.csv`, `.json` or `.jsonl`, with `prompt` plus each `--data_file_keys` field | `unified_dataset.py` |
| Reference image | **`input_image` is hardcoded to `data["video"][0]`** | `parse_extra_inputs` in `train.py` |
| Export | Safetensors per epoch, or per step with `--save_steps`. `--resume_from_checkpoint` exists | `logger.py`, `parsers.py` |

### Consequences

- **Reference from a different frame.** The trainer can't take a separate reference, so the sample builder writes the chosen frame
  as frame 0 of `video`, followed by the target frames. No trainer patch is needed.
- **Control alignment.** With that prefix, video frame `j` shows target frame `j-1`, and the trainer pairs video frame `j` with
  control frame `j-4`. So controls start at target frame 3. This is inferred from the code and needs checking on a real run.

## Adapter compatibility with production (verified)

**DiffSynth's raw keys silently do nothing in Diffusers.** It logs "No LoRA keys ... safe to ignore" and then runs without the adapter, so an adapter could appear in a manifest while having no effect. `motionmirror.backends.adapters.load_adapter` normalizes the keys and counts the attached layers, and raises `AdapterLoadError` unless every layer in the file attached.

On the real model, Diffusers also attaches zero-weight LoRA to the image cross-attention layers, which change nothing. They aren't counted, or every real adapter would be refused. The tiny test model now has the same layers as the real architecture, and a guard test builds the real one from its pinned config with empty weights, confirming all 400 trainer layers exist on it. See `services/ml-worker/tests/contract/test_adapter_key_compat.py`.

Still to confirm with a real adapter: its shapes and effect, and that training on the original checkpoint carries over to the Diffusers conversion.
