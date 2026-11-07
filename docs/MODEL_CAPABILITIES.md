# Model capabilities

Verified against pinned source and by tests. **Real-output quality, memory and speed are `pending GPU validation`.** Contract
tests use tiny random weights, so they prove how the code behaves and nothing about quality.

Evidence: `services/ml-worker/tests/contract/test_wan_animate_tiny.py`, run on CPU and MPS with Diffusers 0.42.1, torch 2.15.0.

## Model

| Item | Value | Source |
| --- | --- | --- |
| Model | `Wan-AI/Wan2.2-Animate-14B-Diffusers` (original weights: `Wan-AI/Wan2.2-Animate-14B`) | [model card](https://huggingface.co/Wan-AI/Wan2.2-Animate-14B) |
| Licence | Apache-2.0 per the model card. Auxiliary tools differ, see `docs/DEPENDENCY_MATRIX.md` | model card |
| Original file sizes | Transformer 34.55 GB, T5 11.36 GB, CLIP 4.77 GB, VAE 0.51 GB | HF file listing |
| Runtime | `diffusers.WanAnimatePipeline`, `mode="replace"` | [docs](https://huggingface.co/docs/diffusers/api/pipelines/wan) |

## Pipeline behaviour

| Behaviour | Result | How it was checked |
| --- | --- | --- |
| Replacement inputs | `image`, `pose_video`, `face_video`, `background_video`, `mask_video` | Docs and tests |
| Mask polarity | White = generate, black = preserve. Internally inverted so known = 1 | Test on the conditioning channel |
| Size | Height and width must be divisible by 16, otherwise `ValueError` | Test |
| Segment length | Should be 4N+1. Other values are silently **floored**, for example 11 becomes 9 | Test |
| Output length | Exactly one frame per pose frame. Shorter input is padded by reflection internally, then trimmed | Test |
| Continuation | `prev_segment_conditioning_frames` decoded frames condition the next segment | Source, test |
| Compositing | **None.** Black-mask pixels are regenerated, so MotionMirror must composite against the source | Test |
| Guidance | `guidance_scale=1.0` disables CFG. A negative prompt then has no effect | Test |
| Guidance enabled | At `>1` a negative prompt changes output. CFG only targets text and face conditioning per the docs | Test, docs |
| Multiple references | One `image`. A list means a batch, not multi-view fusion, so extra photos are stored and marked supplementary | Source |

## Limits the validator must enforce

These crash the pinned pipeline and are caught before any GPU work is spent.

| Rule | What happens otherwise |
| --- | --- |
| `segment_frame_length >= 2 * prev_segment_conditioning_frames` | `RuntimeError` or `AssertionError`. The pipeline trims `out_frames` and reuses it as the next conditioning, so too few frames remain |
| `num_frames > prev_segment_conditioning_frames` | `ValueError: expected a non-empty list of Tensors` |
| `segment_frame_length = 4N+1` | Silent floor. MotionMirror rejects it so effective settings are never a surprise |

## Deviation from the plan

The plan expected a test that white mask regions change while black regions stay nearer the source. Random weights can't show
that, so the test checks the polarity of the conditioning mask directly, which is what the code controls.

## Verified against the real architecture, without weights

The transformer's pinned config sets `added_kv_proj_dim: 5120`, so the real model has image cross-attention layers (`attn2.add_k_proj`, `attn2.add_v_proj`). The tiny test model matches this, checked by a guard test that builds the real architecture with empty weights.

## Real preprocessing, measured on a real clip

Real SAM 2 and ViTPose produce the official control format through this project's selection-driven pipeline. See `docs/BENCHMARKS.md`. Numeric checks on real output: the edit region covers the dancer, skeleton pixels sit on the dancer, and the background is blanked inside the person.

**A known risk to check on a GPU.** With the official settings the edit region is the dancer's whole bounding rectangle: 53% of the frame against the dancer's 32% on a test clip. Anything else inside it is regenerated, including another dancer in a multi-person clip.

## Not yet verified

Real memory use and speed, adapter effect, and any visual quality of generated video: `pending GPU validation`.
