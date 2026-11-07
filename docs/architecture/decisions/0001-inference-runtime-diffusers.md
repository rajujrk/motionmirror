# ADR 0001: Diffusers `WanAnimatePipeline` is the inference runtime

**Status:** accepted, 2026-11-06. Real-output quality is `pending GPU validation`.

**Context.** The brief asks for a verified Diffusers or official runtime for Wan2.2-Animate-14B replacement mode.

**Evidence.** Diffusers 0.42.1 documents `WanAnimatePipeline` with `mode="replace"`, `background_video`, `mask_video`,
`segment_frame_length`, `prev_segment_conditioning_frames`, and a `WanLoraLoaderMixin`. Its source lists
`WanAnimateTransformer3DModel` in the single-file loader map, and its test suite builds a tiny transformer from config.

**Decision.** Use `Wan-AI/Wan2.2-Animate-14B-Diffusers` through `WanAnimatePipeline`, behind `VideoReplacementBackend`.
Contract tests run the real pipeline code on a tiny random-weight model on CPU, so the integration is tested without a GPU.

**Consequences.** Preprocessing is not in Diffusers and is ours (ADR 0003). Adapters trained elsewhere must be shown to
load here (ADR 0002). `Wan2.2-Animate-2-14B` also exists, with different inputs and no model card; it is recorded as a
possible alternative only, per the brief's "compare alternatives only if a real limitation blocks the MVP".
