# ADR 0002: DiffSynth-Studio for real LoRA runs, a small PEFT loop for smoke runs

**Status:** accepted, 2026-11-06. Real training is `pending GPU validation`.

**Evidence.** DiffSynth-Studio's `examples/wanvideo/model_training/lora/Wan2.2-Animate-14B.sh` states verbatim:
`# 1*80G GPU cannot train Wan2.2-Animate-14B LoRA` and `# We tested on 8*80G GPUs`. Its example uses
`--data_file_keys "video,animate_pose_video,animate_face_video"`, so replacement inputs in training need verification.

**Decision.** `gpu-cuda` wraps the pinned DiffSynth trainer in its own image and lockfile. `cpu-smoke` and `mac-mps` run a
small real PEFT loop on a tiny `WanAnimateTransformer3DModel`, labelled smoke. It proves data loading, an optimizer step,
checkpoint, resume and safetensors export, and it cannot satisfy the training gate.

**Consequences.** One config schema and one command set across profiles, as the brief requires. A real run may need a
multi-GPU rental, which is the operator's decision. T0.5 verifies adapter loading and replacement inputs.
