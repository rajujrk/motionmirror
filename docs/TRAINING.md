# Training

Adapter fine-tuning of the pinned Wan-Animate checkpoint. This is neither foundation-model training nor per-user identity
training. **No real LoRA run has been performed: `pending GPU validation`.** Commands and resource measurements are added as the
training track is built.

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

**DiffSynth's raw keys silently do nothing in Diffusers.** It logs "No LoRA keys ... safe to ignore" and then runs without the
adapter, so an adapter could appear in a manifest while having no effect.

`motionmirror.backends.adapters.load_adapter` normalizes the keys and counts the attached layers. It raises `AdapterLoadError` unless
every layer in the file attached. Tests: `services/ml-worker/tests/contract/test_adapter_key_compat.py`, on tiny weights with DiffSynth's
key format. Still to confirm with a real adapter: the real key set from the Animate model.
