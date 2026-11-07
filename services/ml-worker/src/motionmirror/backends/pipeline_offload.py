"""Keep decoded frames off the GPU.

The pinned WanAnimatePipeline keeps every decoded frame on the device until the end. For 900 frames at 720p that is
about 10 GB of GPU memory on top of the model. These wrappers move each decoded segment to the CPU and bring only the
conditioning frames back for the next segment. They touch nothing else, so results are unchanged.
"""

from __future__ import annotations

from typing import Any


def install_decoded_frame_offload(pipe: Any) -> None:
    original_decode = pipe.vae.decode
    original_prepare = pipe.prepare_prev_segment_cond_latents

    def decode(latents: Any, *args: Any, **kwargs: Any) -> Any:
        decoded = original_decode(latents, *args, **kwargs)
        if isinstance(decoded, tuple):
            return tuple(item.to("cpu") for item in decoded)
        decoded.sample = decoded.sample.to("cpu")
        return decoded

    def prepare(prev_segment_cond_video: Any = None, *args: Any, **kwargs: Any) -> Any:
        if prev_segment_cond_video is not None:
            prev_segment_cond_video = prev_segment_cond_video.to(pipe._execution_device)
        return original_prepare(prev_segment_cond_video, *args, **kwargs)

    pipe.vae.decode = decode
    pipe.prepare_prev_segment_cond_latents = prepare
