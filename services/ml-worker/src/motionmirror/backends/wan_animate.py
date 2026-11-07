"""Real replacement with Wan2.2-Animate-14B through Diffusers. Quality and memory are pending GPU validation."""

from __future__ import annotations

import json
import os
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from motionmirror.backends.base import (
    AdapterInput,
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
    JobContext,
    PreparedInputs,
    ValidationResult,
)
from motionmirror.backends.mock import sha256_file
from motionmirror.contracts.settings import first_error
from motionmirror.device import select_device
from motionmirror.media.composite import composite
from motionmirror.media.ffmpeg import MediaError
from motionmirror.media.frames import content_rect, finalize_output, iter_frames, read_frames, write_video
from motionmirror.media.probe import probe

MODEL_ID = "Wan-AI/Wan2.2-Animate-14B-Diffusers"
MODEL_REVISION = "6f4df10861c758af86ac3c979aacc1bf5c03eff0"
CONTROL_NAMES = ("src_pose.mp4", "src_face.mp4", "src_bg.mp4", "src_mask.mp4")
FACE_SIZE = 512


@dataclass(frozen=True)
class WanConfig:
    device: str = "cuda"
    dtype: str = "bfloat16"
    offload: str = "model"  # none | model | sequential
    model_dir: Path | None = None
    transformer_gguf: Path | None = None
    pipeline_kwargs: dict[str, Any] = field(default_factory=dict)


def _load_pipeline(config: WanConfig) -> Any:
    import torch
    from diffusers import AutoencoderKLWan, GGUFQuantizationConfig, WanAnimatePipeline, WanAnimateTransformer3DModel

    from motionmirror.backends.pipeline_offload import install_decoded_frame_offload
    from motionmirror.worker.failures import AttemptFailure

    if config.model_dir is None or not config.model_dir.exists():
        raise AttemptFailure("MODEL_MISSING", "The Wan model isn't installed. Run: make models-download PROFILE=wan")
    dtype = getattr(torch, config.dtype)
    arguments: dict[str, Any] = {}
    if config.transformer_gguf is not None:
        arguments["transformer"] = WanAnimateTransformer3DModel.from_single_file(
            str(config.transformer_gguf),
            quantization_config=GGUFQuantizationConfig(compute_dtype=dtype),
            config=str(config.model_dir),
            subfolder="transformer",
            torch_dtype=dtype,
        )
    # The VAE runs at full precision, as the Diffusers example does.
    vae = AutoencoderKLWan.from_pretrained(
        str(config.model_dir), subfolder="vae", torch_dtype=torch.float32, local_files_only=True
    )
    pipe = WanAnimatePipeline.from_pretrained(
        str(config.model_dir), vae=vae, torch_dtype=dtype, local_files_only=True, use_safetensors=True, **arguments
    )
    if config.offload == "sequential":
        pipe.enable_sequential_cpu_offload(device=config.device)
    elif config.offload == "model":
        pipe.enable_model_cpu_offload(device=config.device)
    else:
        pipe = pipe.to(config.device)
    pipe.vae.enable_tiling()
    install_decoded_frame_offload(pipe)
    return pipe


class WanAnimateReplacementBackend:
    def __init__(self, config: WanConfig, pipeline_factory: Callable[[WanConfig], Any] = _load_pipeline) -> None:
        self.config = config
        self._factory = pipeline_factory
        self._pipe: Any = None
        self._adapter_layers: int | None = None

    @classmethod
    def from_environment(cls) -> WanAnimateReplacementBackend:
        profile = os.environ.get("MM_DEVICE_PROFILE", "cuda")
        selection = select_device(profile)  # type: ignore[arg-type]
        if selection.device == "cpu":
            from motionmirror.device import UnsupportedDeviceError

            raise UnsupportedDeviceError(
                "Wan-Animate can't run on the CPU profile. Use mock mode or a remote GPU worker."
            )
        cache = Path(os.environ.get("MM_MODEL_CACHE", "/models"))
        gguf = os.environ.get("MM_WAN_GGUF")
        return cls(
            WanConfig(
                device=selection.device,
                dtype=os.environ.get("MM_WAN_DTYPE", "bfloat16"),
                offload=os.environ.get("MM_WAN_OFFLOAD", "model"),
                model_dir=cache / "wan-animate-14b",
                transformer_gguf=Path(gguf) if gguf else None,
            )
        )

    def capabilities(self) -> BackendCapabilities:
        real = self.config.device == "cuda"
        return BackendCapabilities(
            backend_id="wan-animate",
            output_label="real" if real else "experimental",
            device_type=self.config.device,
            may_satisfy_acceptance=real,
            supports_negative_prompt=True,
            supports_multiple_references=False,
            continuation="previous-segment-frames",
            precisions=("bfloat16",),
            adapter_loading=True,
        )

    def validate(self, request: GenerationRequest) -> ValidationResult:
        problem = first_error(request.settings)
        if problem:
            return ValidationResult(False, "UNSUPPORTED_CAPABILITY", f"These settings aren't supported: {problem}")
        if request.primary_reference_path is None or not request.primary_reference_path.exists():
            return ValidationResult(False, "INVALID_REQUEST", "The primary reference photo is missing")
        missing = [name for name in CONTROL_NAMES if name not in request.control_paths]
        if missing:
            return ValidationResult(False, "INVALID_REQUEST", f"Run the mask step first. Missing: {', '.join(missing)}")
        return ValidationResult(True)

    def prepare(self, request: GenerationRequest) -> PreparedInputs:
        start = request.inputs.get("trim_start_seconds")
        return PreparedInputs(request=request, trim_start_seconds=float(start) if start is not None else None)

    def _pipeline(self) -> Any:
        if self._pipe is None:
            self._pipe = self._factory(self.config)
        return self._pipe

    def _attach_adapter(self, pipe: Any, adapter: AdapterInput) -> None:
        """Safetensors can't run code when read. load_adapter refuses a file that doesn't fully attach."""
        from safetensors.torch import load_file

        from motionmirror.backends.adapters import load_adapter

        self._adapter_layers = load_adapter(pipe, load_file(str(adapter.path)), "trained", adapter.scale).layer_count

    def generate(self, inputs: PreparedInputs, context: JobContext) -> GenerationResult:
        import torch

        from motionmirror.worker.failures import AttemptFailure

        request = inputs.request
        settings = request.settings
        height, width = int(settings["height"]), int(settings["width"])
        fps = int(settings["processing_fps"])
        timings: dict[str, float] = {}

        context.progress("prepare_reference")
        reference = _fit_reference(request.primary_reference_path, width, height)  # type: ignore[arg-type]

        controls = request.control_paths
        pose = read_frames(controls["src_pose.mp4"], width, height)
        limit = len(pose)
        preview = request.inputs.get("preview_seconds")
        if preview is not None:
            limit = min(limit, int(float(preview) * fps))
        conditioning = int(settings["prev_segment_conditioning_frames"])
        if limit <= conditioning:
            raise AttemptFailure("INVALID_REQUEST", "This clip is too short for these settings")

        pose = pose[:limit]
        face = read_frames(controls["src_face.mp4"], FACE_SIZE, FACE_SIZE)[:limit]
        background = read_frames(controls["src_bg.mp4"], width, height)[:limit]
        masks = read_frames(controls["src_mask.mp4"], width, height)[:limit]
        if not len(pose) == len(face) == len(background) == len(masks):
            raise AttemptFailure(
                "INVALID_REQUEST", "The control videos have different lengths. Run the mask step again"
            )

        segment = int(settings["segment_frame_length"])
        steps = int(settings["num_inference_steps"])
        effective = segment - conditioning
        segments = max(1, (limit - conditioning + effective - 1) // effective)
        started = time.monotonic()
        pipe = self._pipeline()
        if request.adapter is not None:
            self._attach_adapter(pipe, request.adapter)
        timings["model_load_seconds"] = time.monotonic() - started

        done = {"steps": 0}

        def on_step(_pipe: Any, _step: int, _timestep: Any, callback_kwargs: dict[str, Any]) -> dict[str, Any]:
            done["steps"] += 1
            if done["steps"] % steps == 0:
                context.progress("generate", done["steps"] // steps, segments, "segments")
            context.check_cancelled()
            return callback_kwargs

        context.progress("generate", None, segments, "segments")
        started = time.monotonic()
        result = pipe(
            image=reference,
            pose_video=_to_pil(pose),
            face_video=_to_pil(face),
            background_video=_to_pil(background),
            mask_video=[Image.fromarray(frame[:, :, 0]) for frame in masks],
            prompt=settings.get("positive_prompt") or "",
            negative_prompt=settings.get("negative_prompt") or None,
            height=height,
            width=width,
            segment_frame_length=segment,
            prev_segment_conditioning_frames=conditioning,
            num_inference_steps=steps,
            guidance_scale=float(settings["guidance_scale"]),
            mode="replace",
            generator=torch.Generator("cpu").manual_seed(int(settings["seed"])),
            output_type="np",
            callback_on_step_end=on_step,
            callback_on_step_end_tensor_inputs=[],
            **self.config.pipeline_kwargs,
        )
        generated = result.frames[0]
        timings["generate_seconds"] = time.monotonic() - started
        if len(generated) != limit:
            raise AttemptFailure(
                "INTERNAL_ERROR", f"The model returned {len(generated)} frames, expected {limit}", True
            )

        context.progress("composite", None, limit, "frames")
        info = probe(request.source_path)
        duration = limit / fps
        rect = content_rect(info.display_width, info.display_height, width, height)
        feather = int(settings.get("mask_feather_pixels") or 0)
        source_frames = iter_frames(
            request.source_path, width, height, fps=fps, start=inputs.trim_start_seconds, duration=duration
        )

        def blended() -> Any:
            x, y, w, h = rect
            for index, source in enumerate(source_frames):
                if index >= limit:
                    return
                frame = (np.clip(generated[index], 0, 1) * 255 + 0.5).astype(np.uint8)
                yield composite(source, frame, masks[index][:, :, 0], feather)[y : y + h, x : x + w]

        started = time.monotonic()
        silent = context.workspace / "video_only.mp4"
        written = write_video(blended(), silent, fps)
        timings["composite_seconds"] = time.monotonic() - started
        if written != limit:
            raise AttemptFailure("INTERNAL_ERROR", f"Wrote {written} frames, expected {limit}", True)

        context.progress("encode")
        output = context.workspace / "result.mp4"
        keep_audio = bool(settings.get("retain_audio", True) and info.audio_streams)
        finalize_output(silent, request.source_path, output, inputs.trim_start_seconds, duration, keep_audio)

        context.progress("verify")
        final = probe(output)
        if abs(final.duration_seconds - duration) > 1 / fps + 0.05:
            raise MediaError(f"The output is {final.duration_seconds:.3f}s long, expected {duration:.3f}s")
        manifest = context.workspace / "manifest.json"
        manifest.write_text(json.dumps(self._manifest(request, final, output, timings, limit), indent=2))
        effective = self._effective(request, limit, segments)
        return GenerationResult(video_path=output, manifest_path=manifest, effective_settings=effective)

    def _effective(self, request: GenerationRequest, frames: int, segments: int) -> dict[str, Any]:
        return {
            "backend": "wan-animate",
            "device_type": self.config.device,
            "model": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "frames": frames,
            "segments": segments,
            "adapter": request.adapter.name if request.adapter else None,
            "adapter_license_scope": request.adapter.license_scope if request.adapter else None,
        }

    def _manifest(
        self, request: GenerationRequest, final: Any, output: Path, timings: dict[str, float], frames: int
    ) -> dict[str, Any]:
        import diffusers
        import torch

        capabilities = self.capabilities()
        peak = torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None
        return {
            "output_label": capabilities.output_label,
            "backend_id": capabilities.backend_id,
            "device_type": capabilities.device_type,
            "dtype": self.config.dtype,
            "may_satisfy_acceptance": capabilities.may_satisfy_acceptance,
            "model": {
                "id": MODEL_ID,
                "revision": MODEL_REVISION,
                "gguf": str(self.config.transformer_gguf) if self.config.transformer_gguf else None,
            },
            "runtime": {
                "diffusers": diffusers.__version__,
                "torch": torch.__version__,
                "python": platform.python_version(),
            },
            "adapter": {
                "id": request.adapter.adapter_id,
                "name": request.adapter.name,
                "sha256": request.adapter.sha256,
                "scale": request.adapter.scale,
                "license_scope": request.adapter.license_scope,
                "layers_attached": self._adapter_layers,
            }
            if request.adapter
            else None,
            "settings": {key: value for key, value in request.settings.items() if "prompt" not in key},
            "prompts_recorded": False,
            "references": {
                "consumed": [request.inputs.get("primary_reference_asset_id")],
                "stored_not_consumed": list(request.inputs.get("supplementary_reference_asset_ids") or []),
            },
            "input_checksums": {
                "source": request.inputs.get("source_sha256"),
                "primary_reference": request.inputs.get("primary_reference_sha256"),
            },
            "output_checksum": sha256_file(output),
            "output": {
                "frames": frames,
                "duration_seconds": final.duration_seconds,
                "width": final.display_width,
                "height": final.display_height,
                "audio_streams": final.audio_streams,
                "video_codec": final.video_codec,
                "pixel_format": final.pixel_format,
            },
            "timings": timings,
            "peak_gpu_memory_bytes": peak,
            "compositing": "generated pixels blended over the original with the feathered edit mask",
        }

    def release(self) -> None:
        self._pipe = None


def _to_pil(frames: list[np.ndarray]) -> list[Image.Image]:
    return [Image.fromarray(frame) for frame in frames]


def _fit_reference(path: Path, width: int, height: int) -> Image.Image:
    """Padded to the canvas like the official script, so a full-body photo is never squashed."""
    return Image.fromarray(next(iter_frames(path, width, height)))
