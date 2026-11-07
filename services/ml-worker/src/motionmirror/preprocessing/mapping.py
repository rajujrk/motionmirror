"""From the user's marks (normalized display coordinates, source frames) to model-space prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any


class SelectionOutsideClip(ValueError):
    """Every mark is outside the trimmed range, so there's nothing to start tracking from."""


@dataclass
class FramePrompt:
    frame: int
    points: list[tuple[float, float]] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)
    box: tuple[float, float, float, float] | None = None


def to_model_point(x: float, y: float, source: tuple[int, int], model: tuple[int, int]) -> tuple[float, float]:
    """Matches sourceToModel in the web app: keep the aspect ratio, centre, pad."""
    source_width, source_height = source
    model_width, model_height = model
    scale = min(model_width / source_width, model_height / source_height)
    pad_x = (model_width - source_width * scale) / 2
    pad_y = (model_height - source_height * scale) / 2
    return x * source_width * scale + pad_x, y * source_height * scale + pad_y


def processing_frame(source_frame: int, source_rate: Fraction, fps: int, trim_start: float, total: int) -> int | None:
    """Which processing frame shows this source frame, by time. None if it falls outside the range."""
    seconds = float(source_frame / source_rate) - trim_start
    index = round(seconds * fps)
    return index if 0 <= index < total else None


def build_prompts(
    annotations: dict[str, Any],
    source: tuple[int, int],
    model: tuple[int, int],
    source_rate: Fraction,
    fps: int,
    trim_start: float,
    total: int,
    default_frame: int,
) -> list[FramePrompt]:
    prompts: dict[int, FramePrompt] = {}

    def prompt_for(raw_frame: int | None) -> FramePrompt | None:
        index = processing_frame(default_frame if raw_frame is None else raw_frame, source_rate, fps, trim_start, total)
        return None if index is None else prompts.setdefault(index, FramePrompt(index))

    for point in annotations.get("points") or []:
        prompt = prompt_for(point.get("frame"))
        if prompt is not None:
            prompt.points.append(to_model_point(point["x"], point["y"], source, model))
            prompt.labels.append(1 if point.get("positive", True) else 0)

    box = annotations.get("box")
    if isinstance(box, dict):
        prompt = prompt_for(box.get("frame"))
        if prompt is not None:
            left, top = to_model_point(box["x"], box["y"], source, model)
            right, bottom = to_model_point(box["x"] + box["width"], box["y"] + box["height"], source, model)
            prompt.box = (left, top, right, bottom)

    usable = [p for p in prompts.values() if p.box is not None or any(label == 1 for label in p.labels)]
    if not usable:
        raise SelectionOutsideClip("Add a mark on the dancer inside the selected part of the clip")
    return sorted(prompts.values(), key=lambda prompt: prompt.frame)
