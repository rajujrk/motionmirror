"""Selection-driven preprocessing: the user's marks choose the dancer, and everything else follows the mask.

The official Wan script assumes one person in the shot, so it can't pick one dancer among several. Its output format
is kept exactly: src_pose, src_face, src_bg and src_mask.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from motionmirror.backends.base import JobContext
from motionmirror.media.ffmpeg import MediaError
from motionmirror.media.frames import read_frames, write_video
from motionmirror.media.probe import probe
from motionmirror.preprocessing.mapping import FramePrompt, SelectionOutsideClip, build_prompts
from motionmirror.preprocessing.masks import (
    DEFAULT_EXPAND_PIXELS,
    background_frame,
    bbox_from_mask,
    expand_mask,
    mask_frame,
)
from motionmirror.preprocessing.tracking import TargetLost, assess_track
from motionmirror.worker.failures import AttemptFailure

FACE_SIZE = 512
PREVIEW_TINT = np.array([212, 17, 95], dtype=np.float32)


class Tracker(Protocol):
    revision: str

    def track(self, frames: list[np.ndarray], prompts: list[FramePrompt]) -> list[np.ndarray]: ...


class PoseEstimator(Protocol):
    revision: str

    def estimate(
        self, frames: list[np.ndarray], boxes: list[tuple[int, int, int, int] | None]
    ) -> list[dict[str, Any]]: ...

    def face_crop(self, frame: np.ndarray, meta: dict[str, Any]) -> np.ndarray | None: ...

    def draw(self, meta: dict[str, Any], height: int, width: int) -> np.ndarray: ...


def clip_range(inputs: dict[str, Any], duration: float) -> tuple[float | None, float]:
    start = inputs.get("trim_start_seconds")
    end = inputs.get("trim_end_seconds")
    begin = float(start) if start is not None else 0.0
    stop = float(end) if end is not None else duration
    return (begin if start is not None else None), stop - begin


def _hold_last(items: list[Any]) -> list[Any]:
    """Fill gaps from the nearest earlier item, or the first later one at the start of a clip."""
    first = next((item for item in items if item is not None), None)
    held: list[Any] = []
    last = first
    for item in items:
        last = item if item is not None else last
        held.append(last)
    return held


def run_preprocess(
    source: Path,
    inputs: dict[str, Any],
    settings: dict[str, Any],
    context: JobContext,
    tracker: Tracker | None = None,
    pose: PoseEstimator | None = None,
) -> dict[str, Any]:
    timings: dict[str, float] = {}
    started = time.monotonic()
    context.progress("decode")
    try:
        info = probe(source)
    except MediaError as error:
        raise AttemptFailure("INVALID_MEDIA", "This video couldn't be read") from error
    height, width, fps = int(settings["height"]), int(settings["width"]), int(settings["processing_fps"])
    start, duration = clip_range(inputs, info.duration_seconds)
    try:
        frames = read_frames(source, width, height, fps=fps, start=start, duration=duration)
    except MediaError as error:
        raise AttemptFailure("INVALID_MEDIA", "This video couldn't be read") from error
    if not frames:
        raise AttemptFailure("INVALID_MEDIA", "No frames could be read from the selected part of this clip")
    timings["decode_seconds"] = time.monotonic() - started

    try:
        prompts = build_prompts(
            inputs.get("annotations") or {},
            (info.display_width, info.display_height),
            (width, height),
            info.frame_rate,
            fps,
            start or 0.0,
            len(frames),
            int(inputs.get("frame_index") or 0),
        )
    except SelectionOutsideClip as error:
        raise AttemptFailure("INVALID_REQUEST", str(error)) from error

    if tracker is None or pose is None:
        from motionmirror.preprocessing.models import load_pose_estimator, load_tracker

        tracker = tracker or load_tracker()
        pose = pose or load_pose_estimator()

    context.progress("segment", 1, len(frames), "frames")
    context.check_cancelled()
    step = time.monotonic()
    masks = tracker.track(frames, prompts)
    timings["segment_seconds"] = time.monotonic() - step
    try:
        report = assess_track(masks)
    except TargetLost as error:
        raise AttemptFailure("TARGET_LOST", str(error)) from error

    context.progress("extract_motion", 1, len(frames), "frames")
    context.check_cancelled()
    step = time.monotonic()
    lost = set(report.lost_frames)
    boxes = [None if index in lost else bbox_from_mask(mask) for index, mask in enumerate(masks)]
    metas = _hold_last(pose.estimate(frames, boxes))
    faces = _hold_last([pose.face_crop(frame, meta) for frame, meta in zip(frames, metas, strict=True)])
    timings["pose_seconds"] = time.monotonic() - step

    blank_face = np.zeros((FACE_SIZE, FACE_SIZE, 3), dtype=np.uint8)
    expand = int(settings.get("mask_expand_pixels", DEFAULT_EXPAND_PIXELS))
    edit_masks = [expand_mask(mask, expand) for mask in masks]

    output_dir = context.workspace
    outputs = {
        "src_pose.mp4": (pose.draw(meta, height, width) for meta in metas),
        "src_face.mp4": (face if face is not None else blank_face for face in faces),
        "src_bg.mp4": (background_frame(frame, mask) for frame, mask in zip(frames, edit_masks, strict=True)),
        "src_mask.mp4": (mask_frame(mask) for mask in edit_masks),
        "preview.mp4": (_preview_frame(frame, mask) for frame, mask in zip(frames, masks, strict=True)),
    }
    described = []
    for name, stream in outputs.items():
        written = write_video(stream, output_dir / name, fps)
        if written != len(frames):
            raise AttemptFailure("INTERNAL_ERROR", f"{name} has {written} frames, expected {len(frames)}", True)
        described.append({"name": name, "path": str(output_dir / name), "content_type": "video/mp4"})

    tool_revision = f"{tracker.revision}+{pose.revision}"
    track = report.as_dict()
    manifest_path = output_dir / "preprocess_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "output_label": "real",
                "tool_revision": tool_revision,
                "device_type": os.environ.get("MM_DEVICE_PROFILE", "cpu"),
                "selection_revision": inputs.get("selection_revision"),
                "frames": len(frames),
                "fps": fps,
                "size": {"width": width, "height": height},
                "track": track,
                "timings": timings,
                "mask_polarity": "white = generate, black = preserve",
            },
            indent=2,
        )
    )
    described.append(
        {"name": "preprocess_manifest.json", "path": str(manifest_path), "content_type": "application/json"}
    )
    return {
        "outputs": described,
        "result": {"tool_revision": tool_revision, "output_label": "real"},
        "effective_settings": {"track": track, "frames": len(frames)},
    }


def _preview_frame(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """The original with the tracked dancer tinted, so a drift onto someone else is obvious."""
    tinted = frame.astype(np.float32)
    selected = mask.astype(bool)
    tinted[selected] = tinted[selected] * 0.45 + PREVIEW_TINT * 0.55
    return tinted.astype(np.uint8)
