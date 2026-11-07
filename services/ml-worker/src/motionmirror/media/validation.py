"""Bounded checks for uploads. Passing them doesn't make a file trustworthy, only safe to keep processing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from motionmirror.media.ffmpeg import MediaError, file_input, run_ffmpeg
from motionmirror.media.probe import probe
from motionmirror.media.shots import detect_cuts

MAX_DIMENSION = 8192
TRIM_FACTOR = 10
DECODE_CHECK_SECONDS = 2
DEFAULT_MAX_PIXELS = 64_000_000
LOW_RESOLUTION_EDGE = 512
RASTER_FORMATS = {"JPEG", "PNG", "WEBP"}


class RejectedMedia(ValueError):
    """The upload isn't acceptable. The message is safe to show the user."""


def validate_video(path: Path, max_seconds: float) -> dict[str, Any]:
    try:
        info = probe(path)
    except MediaError as error:
        raise RejectedMedia("This file isn't a readable video with a video track") from error
    if max(info.width, info.height) > MAX_DIMENSION:
        raise RejectedMedia(f"This video is larger than {MAX_DIMENSION} pixels on a side")
    if info.duration_seconds <= 0:
        raise RejectedMedia("This video has no duration")
    if info.duration_seconds > max_seconds * TRIM_FACTOR:
        raise RejectedMedia("This video is too long to trim here. Upload a shorter clip")
    try:
        run_ffmpeg(
            ["-t", str(DECODE_CHECK_SECONDS), "-i", file_input(path), "-map", "0:v:0", "-f", "null", "-"], timeout=120
        )
    except MediaError as error:
        raise RejectedMedia("This video can't be decoded") from error
    try:
        cuts = detect_cuts(path)
    except MediaError as error:
        # Unknown isn't the same as none: tracking must never cross a cut no one looked for.
        raise RejectedMedia("This video couldn't be checked for cuts") from error
    warnings = ["VARIABLE_FRAME_RATE"] if info.is_vfr else []
    if cuts:
        warnings.append("MULTIPLE_SHOTS")
    return {
        "cuts": cuts,
        "width": info.width,
        "height": info.height,
        "display_width": info.display_width,
        "display_height": info.display_height,
        "rotation": info.rotation,
        "frame_rate": f"{info.frame_rate.numerator}/{info.frame_rate.denominator}",
        "average_frame_rate": f"{info.average_frame_rate.numerator}/{info.average_frame_rate.denominator}",
        "time_base": f"{info.time_base.numerator}/{info.time_base.denominator}",
        "duration_seconds": info.duration_seconds,
        "is_vfr": info.is_vfr,
        "audio_streams": info.audio_streams,
        "video_codec": info.video_codec,
        "frame_count": info.frame_count,
        "needs_trim": info.duration_seconds > max_seconds,
        "warnings": warnings,
    }


def validate_image(path: Path, output: Path, max_pixels: int = DEFAULT_MAX_PIXELS) -> dict[str, Any]:
    """Normalize to RGB PNG: orientation applied, every other piece of metadata dropped."""
    try:
        with Image.open(path) as image:
            if image.format not in RASTER_FORMATS:
                raise RejectedMedia("Upload a JPEG, PNG or WebP photo")
            # Checked before decoding, so a small file can't expand into huge memory use.
            width, height = image.size
            if width * height > max_pixels:
                raise RejectedMedia(f"This photo has more than {max_pixels} pixels")
            upright = ImageOps.exif_transpose(image).convert("RGB")
    except RejectedMedia:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise RejectedMedia("This file isn't a readable photo") from error

    clean = Image.new("RGB", upright.size)
    clean.paste(upright)
    clean.save(output, "PNG")
    warnings = ["LOW_RESOLUTION"] if min(clean.size) < LOW_RESOLUTION_EDGE else []
    return {"width": clean.width, "height": clean.height, "source_format": image.format, "warnings": warnings}
