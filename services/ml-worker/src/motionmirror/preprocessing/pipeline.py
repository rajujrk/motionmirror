"""Selection-driven preprocessing: the user's marks choose the dancer, and everything else follows the mask.

The official Wan script assumes one person in the shot, so it can't pick one dancer among several. Its output format
is kept exactly: src_pose, src_face, src_bg and src_mask.

Memory stays bounded by clip length. Decoding is cheap next to tracking, so frames are decoded again rather than kept,
and SAM 2 runs in chunks because its session holds a float copy of every frame it has seen. On a 16 GB Mac, holding a
27 second clip in memory drove swap up by 6.5 GB before tracking finished.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from motionmirror.backends.base import JobContext
from motionmirror.media.ffmpeg import MediaError
from motionmirror.media.frames import FrameSource, VideoWriter
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
# Long enough to keep tracking steady and short enough to bound memory. Measured in docs/MAC_APPLE_SILICON.md.
CHUNK_FRAMES = 120
PROGRESS_EVERY = 30


class Tracker(Protocol):
    revision: str

    def track(self, frames: list[np.ndarray], prompts: list[FramePrompt]) -> list[np.ndarray]: ...


class PoseEstimator(Protocol):
    revision: str

    def estimate(self, frame: np.ndarray, box: tuple[int, int, int, int]) -> dict[str, Any]: ...

    def face_crop(self, frame: np.ndarray, meta: dict[str, Any]) -> np.ndarray | None: ...

    def draw(self, meta: dict[str, Any], height: int, width: int) -> np.ndarray: ...


def clip_range(inputs: dict[str, Any], duration: float) -> tuple[float | None, float]:
    start = inputs.get("trim_start_seconds")
    end = inputs.get("trim_end_seconds")
    begin = float(start) if start is not None else 0.0
    stop = float(end) if end is not None else duration
    return (begin if start is not None else None), stop - begin


def _seed(masks: list[np.ndarray | None], near: range) -> np.ndarray | None:
    """The closest mask that still shows the dancer, so someone hidden across a chunk boundary isn't lost for good."""
    for index in near:
        mask = masks[index]
        if mask is not None and mask.any():
            return mask
    return None


def track_in_chunks(
    tracker: Tracker,
    source: FrameSource,
    total: int,
    prompts: list[FramePrompt],
    chunk_frames: int = CHUNK_FRAMES,
    on_progress: Callable[[int], None] | None = None,
) -> list[np.ndarray]:
    """Forward from the first mark to the end, then back to the start. Each chunk starts from the last one's mask.

    Chunks share one frame. That frame already has a mask from the chunk before, and it's kept rather than replaced.
    """
    masks: list[np.ndarray | None] = [None] * total
    first = prompts[0].frame

    def run(begin: int, end: int, seed_frame: int | None) -> None:
        local = [replace(prompt, frame=prompt.frame - begin) for prompt in prompts if begin <= prompt.frame < end]
        seed = None
        if seed_frame is not None:
            # A mark from the user on the shared frame says more than the tracker's own mask, and SAM 2 can't take both.
            if not any(prompt.frame == seed_frame - begin for prompt in local):
                nearby = range(seed_frame, -1, -1) if seed_frame >= first else range(seed_frame, total)
                seed = _seed(masks, nearby)
            if seed is not None:
                local.append(FramePrompt(seed_frame - begin, mask=seed))
        if not local:
            # Nothing visible to continue from, so these frames stay empty and count as lost.
            for index in range(begin, end):
                masks[index] = masks[index] if masks[index] is not None else np.zeros_like(masks[seed_frame or 0])
            return
        frames = source.slice(begin, end)
        for offset, mask in enumerate(tracker.track(frames, sorted(local, key=lambda prompt: prompt.frame))):
            # Past frames' results stay: only a user's mark on the shared frame can replace one.
            if masks[begin + offset] is None or seed is None:
                masks[begin + offset] = mask
        if on_progress:
            on_progress(sum(mask is not None for mask in masks))

    begin = first
    run(begin, min(begin + chunk_frames, total), None)
    while begin + chunk_frames < total:
        begin += chunk_frames - 1
        run(begin, min(begin + chunk_frames, total), begin)

    # Everything before the first mark, walking backward.
    end = first + 1
    while end > 1:
        begin = max(end - chunk_frames, 0)
        run(begin, end, end - 1)
        end = begin + 1

    return [mask for mask in masks if mask is not None]


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
    height, width, fps = int(settings["height"]), int(settings["width"]), int(settings["processing_fps"])
    try:
        info = probe(source)
        start, duration = clip_range(inputs, info.duration_seconds)
        frames = FrameSource(source, width, height, fps, start, duration)
        total = frames.count()
    except MediaError as error:
        raise AttemptFailure("INVALID_MEDIA", "This video couldn't be read") from error
    if not total:
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
            total,
            int(inputs.get("frame_index") or 0),
        )
    except SelectionOutsideClip as error:
        raise AttemptFailure("INVALID_REQUEST", str(error)) from error

    if tracker is None or pose is None:
        from motionmirror.preprocessing.models import load_pose_estimator, load_tracker

        tracker = tracker or load_tracker()
        pose = pose or load_pose_estimator()

    def tracked(done: int) -> None:
        context.progress("segment", done, total, "frames")
        context.check_cancelled()

    context.progress("segment", 0, total, "frames")
    step = time.monotonic()
    masks = track_in_chunks(tracker, frames, total, prompts, on_progress=tracked)
    timings["segment_seconds"] = time.monotonic() - step
    try:
        report = assess_track(masks)
    except TargetLost as error:
        raise AttemptFailure("TARGET_LOST", str(error)) from error

    step = time.monotonic()
    context.progress("extract_motion", 0, total, "frames")
    described = _write_controls(frames, masks, set(report.lost_frames), pose, settings, context)
    timings["motion_and_encode_seconds"] = time.monotonic() - step

    tool_revision = f"{tracker.revision}+{pose.revision}"
    device = os.environ.get("MM_DEVICE_PROFILE", "cpu")
    label = "real" if device == "cuda" else "experimental"
    track = report.as_dict()
    manifest_path = context.workspace / "preprocess_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "output_label": label,
                "tool_revision": tool_revision,
                "device_type": device,
                "selection_revision": inputs.get("selection_revision"),
                "frames": total,
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
        "result": {"tool_revision": tool_revision, "output_label": label},
        "effective_settings": {"track": track, "frames": total, "device_type": device},
    }


def _write_controls(
    frames: FrameSource,
    masks: list[np.ndarray],
    lost: set[int],
    pose: PoseEstimator,
    settings: dict[str, Any],
    context: JobContext,
) -> list[dict[str, str]]:
    """One pass over the clip. The pose model is never given a hidden frame, so it can't pick someone else.

    A hidden dancer keeps their last known pose, and hidden frames at the very start take the first one seen.
    """
    total = len(masks)
    height, width = masks[0].shape[:2]
    fps = int(settings["processing_fps"])
    expand = int(settings.get("mask_expand_pixels", DEFAULT_EXPAND_PIXELS))
    first_visible = next(index for index in range(total) if index not in lost)
    opening = frames.slice(first_visible, first_visible + 1)[0]
    first_box = bbox_from_mask(masks[first_visible])
    assert first_box is not None, "a frame that isn't lost has a mask"
    meta = pose.estimate(opening, first_box)
    face = pose.face_crop(opening, meta)
    blank_face = np.zeros((FACE_SIZE, FACE_SIZE, 3), dtype=np.uint8)

    names = ("src_pose.mp4", "src_face.mp4", "src_bg.mp4", "src_mask.mp4", "preview.mp4")
    writers = {name: VideoWriter(context.workspace / name, fps) for name in names}
    try:
        for index, frame in enumerate(frames):
            mask = masks[index]
            if index not in lost:
                if index != first_visible:
                    box = bbox_from_mask(mask)
                    assert box is not None, "a frame that isn't lost has a mask"
                    meta = pose.estimate(frame, box)
                crop = pose.face_crop(frame, meta)
                face = crop if crop is not None else face
            edit = expand_mask(mask, expand)
            writers["src_pose.mp4"].write(pose.draw(meta, height, width))
            writers["src_face.mp4"].write(face if face is not None else blank_face)
            writers["src_bg.mp4"].write(background_frame(frame, edit))
            writers["src_mask.mp4"].write(mask_frame(edit))
            writers["preview.mp4"].write(_preview_frame(frame, mask))
            if (index + 1) % PROGRESS_EVERY == 0:
                context.progress("extract_motion", index + 1, total, "frames")
                context.check_cancelled()
    except BaseException:
        for writer in writers.values():
            writer.abort()
        raise

    described = []
    for name, writer in writers.items():
        written = writer.close()
        if written != total:
            raise AttemptFailure("INTERNAL_ERROR", f"{name} has {written} frames, expected {total}", True)
        described.append({"name": name, "path": str(context.workspace / name), "content_type": "video/mp4"})
    return described


def _preview_frame(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """The original with the tracked dancer tinted, so a drift onto someone else is obvious."""
    tinted = frame.astype(np.float32)
    selected = mask.astype(bool)
    tinted[selected] = tinted[selected] * 0.45 + PREVIEW_TINT * 0.55
    return tinted.astype(np.uint8)
