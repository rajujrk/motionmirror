"""What a file really contains, from its content rather than its name or MIME type."""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from motionmirror.media.ffmpeg import MediaError, run_ffprobe

_VFR_TOLERANCE = 0.01


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    display_width: int
    display_height: int
    rotation: int
    frame_rate: Fraction
    average_frame_rate: Fraction
    time_base: Fraction
    duration_seconds: float
    is_vfr: bool
    audio_streams: int
    video_codec: str
    pixel_format: str
    frame_count: int | None


def _rational(text: str | None) -> Fraction:
    if not text or text in {"0/0", "N/A"}:
        return Fraction(0)
    return Fraction(text)


def _rotation(stream: dict[str, Any]) -> int:
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            return int(side_data["rotation"])
    return 0


def probe(path: Path | str) -> MediaInfo:
    data = json.loads(run_ffprobe(path))
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("no video stream")
    frame_rate = _rational(video.get("r_frame_rate"))
    average = _rational(video.get("avg_frame_rate"))
    rotation = _rotation(video)
    width, height = int(video["width"]), int(video["height"])
    swapped = abs(rotation) % 180 == 90
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    is_vfr = bool(frame_rate and average and abs(frame_rate - average) / frame_rate > _VFR_TOLERANCE)
    frames = video.get("nb_frames")
    return MediaInfo(
        width=width,
        height=height,
        display_width=height if swapped else width,
        display_height=width if swapped else height,
        rotation=rotation,
        frame_rate=frame_rate,
        average_frame_rate=average,
        time_base=_rational(video.get("time_base")),
        duration_seconds=duration,
        is_vfr=is_vfr,
        audio_streams=sum(1 for s in streams if s.get("codec_type") == "audio"),
        video_codec=str(video.get("codec_name")),
        pixel_format=str(video.get("pix_fmt")),
        frame_count=int(frames) if frames and str(frames).isdigit() else None,
    )
