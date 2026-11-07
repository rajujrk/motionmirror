"""Deterministic fixture processing for development and tests. It does no AI replacement and says so."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from motionmirror.backends.base import (
    BackendCapabilities,
    GenerationRequest,
    GenerationResult,
    JobContext,
    PreparedInputs,
    ValidationResult,
)
from motionmirror.media.ffmpeg import file_input, run_ffmpeg
from motionmirror.media.probe import MediaInfo, probe

MOCK_LABEL = "MOCK / NO AI REPLACEMENT"
_BANNER_COLOR = (200, 30, 30)
_MIN_BANNER_HEIGHT = 28


def make_banner(width: int, height: int, path: Path, text: str = MOCK_LABEL) -> Path:
    banner_height = max(_MIN_BANNER_HEIGHT, height // 12)
    image = Image.new("RGB", (width, banner_height), _BANNER_COLOR)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(10, int(banner_height * 0.55)))
    while draw.textlength(text, font=font) > width - 8 and font.size > 8:
        font = ImageFont.load_default(size=font.size - 1)
    text_width = draw.textlength(text, font=font)
    draw.text(((width - text_width) / 2, banner_height * 0.18), text, fill=(255, 255, 255), font=font)
    image.save(path)
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def clip_selection(inputs: dict[str, Any], info: MediaInfo) -> tuple[float | None, float | None]:
    """The trim as (start, duration), with a preview limit applied after the trim."""
    start = inputs.get("trim_start_seconds")
    end = inputs.get("trim_end_seconds")
    begin = float(start) if start is not None else 0.0
    duration = float(end) - begin if end is not None else info.duration_seconds - begin
    preview = inputs.get("preview_seconds")
    if preview is not None:
        duration = min(duration, float(preview))
    selected_start = begin if start is not None else None
    selected_duration = duration if end is not None or preview is not None else None
    return selected_start, selected_duration


def encode_with_banner(source: Path, banner: Path, output: Path, start: float | None, duration: float | None) -> None:
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.6f}"]
    if duration is not None:
        args += ["-t", f"{duration:.6f}"]
    args += [
        "-i", file_input(source),
        "-i", file_input(banner),
        "-filter_complex", "[0:v:0][1:v:0]overlay=0:0:format=auto,format=yuv420p[out]",
        "-map", "[out]", "-map", "0:a:0?",
        "-map_metadata", "-1",
        "-fps_mode", "passthrough",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output),
    ]  # fmt: skip
    run_ffmpeg(args)


class MockReplacementBackend:
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            backend_id="mock",
            output_label="mock",
            device_type="cpu",
            may_satisfy_acceptance=False,
        )

    def validate(self, request: GenerationRequest) -> ValidationResult:
        if not request.source_path.exists():
            return ValidationResult(False, "INVALID_REQUEST", "The source video is missing")
        return ValidationResult(True)

    def prepare(self, request: GenerationRequest) -> PreparedInputs:
        start, duration = clip_selection(request.inputs, probe(request.source_path))
        return PreparedInputs(request=request, trim_start_seconds=start, duration_seconds=duration)

    def generate(self, inputs: PreparedInputs, context: JobContext) -> GenerationResult:
        request = inputs.request
        info = probe(request.source_path)
        context.progress("generate")
        context.check_cancelled()

        context.progress("composite")
        banner = make_banner(info.display_width, info.display_height, context.workspace / "banner.png")

        context.progress("encode")
        output = context.workspace / "result.mp4"
        encode_with_banner(request.source_path, banner, output, inputs.trim_start_seconds, inputs.duration_seconds)

        context.progress("verify")
        result = probe(output)
        manifest = context.workspace / "manifest.json"
        manifest.write_text(json.dumps(self._manifest(request, result, output), indent=2))
        return GenerationResult(video_path=output, manifest_path=manifest, effective_settings={"backend": "mock"})

    @staticmethod
    def _manifest(request: GenerationRequest, result: MediaInfo, output: Path) -> dict[str, Any]:
        settings = {key: value for key, value in request.settings.items() if "prompt" not in key}
        return {
            "label": MOCK_LABEL,
            "output_label": "mock",
            "backend_id": "mock",
            "device_type": "cpu",
            "model": None,
            "note": "Deterministic fixture output for testing. No person was replaced.",
            "settings": settings,
            "prompts_recorded": False,
            "references": {
                "consumed": [],
                "stored_not_consumed": list(request.inputs.get("supplementary_reference_asset_ids") or []),
            },
            "input_checksums": {
                "source": request.inputs.get("source_sha256"),
                "primary_reference": request.inputs.get("primary_reference_sha256"),
            },
            "output_checksum": sha256_file(output),
            "output": {
                "duration_seconds": result.duration_seconds,
                "width": result.display_width,
                "height": result.display_height,
                "audio_streams": result.audio_streams,
                "video_codec": result.video_codec,
                "pixel_format": result.pixel_format,
            },
        }

    def release(self) -> None:
        pass
