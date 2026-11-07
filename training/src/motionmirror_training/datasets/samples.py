"""Build replacement-mode training samples from a dataset archive, using the same preprocessing as serving.

The pinned trainer (DiffSynth at the commit in docs/TRAINING.md) has two habits that shape a sample:

- It takes its reference image from frame 0 of the video, so a reference from elsewhere in the clip is written there.
- It trims controls to len(video) - 4 and pairs video frame j with control frame j - 4. With the reference prefix,
  video frame j shows target frame j - 1, so control k must show target frame k + 3.

That alignment is inferred from the trainer's code and needs checking on a real run.
"""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from motionmirror.backends.base import JobContext
from motionmirror.media.frames import read_frames, write_video
from motionmirror.preprocessing.mapping import FramePrompt
from motionmirror.preprocessing.pipeline import PoseEstimator, run_preprocess

from motionmirror_training.datasets.registry import DatasetEntry, Purpose, assert_training_allowed
from motionmirror_training.datasets.tiktok import (
    DATASET_FPS,
    ClipIndex,
    extract_clip,
    index_clips,
    read_png,
)

# 4N+1, as the model requires. One video frame is the reference, so 80 target frames give 77 controls, which is the
# segment length the presets use.
VIDEO_FRAMES = 81
TARGET_FRAMES = VIDEO_FRAMES - 1
CONTROL_OFFSET = 3
CONTROL_FRAMES = TARGET_FRAMES - CONTROL_OFFSET
# The reference must not be a frame the model is also asked to produce.
REFERENCE_GAP = 8
MIN_SOURCE_FRAMES = TARGET_FRAMES + REFERENCE_GAP + 1
MIN_SIDE = 256
MIN_COVERAGE = 0.02
MAX_COVERAGE = 0.85
MAX_LOST_FRACTION = 0.1
SPLIT_SHARES = {"train": 0.8, "val": 0.1, "test": 0.1}
NEAR_DUPLICATE_BITS = 6
CANVAS = {"width": 432, "height": 768}


class SplitLeakError(RuntimeError):
    """The same source appears on both sides of a split, which would make evaluation meaningless."""


@dataclass(frozen=True)
class Rejection:
    clip_id: str
    reason: str


@dataclass(frozen=True)
class SampleRow:
    clip_id: str
    split: str
    source_group: str
    person_id: str
    source_sha256: str
    reference_frame: int
    target_start_frame: int
    video: str
    animate_pose_video: str
    animate_face_video: str
    animate_inpaint_video: str
    animate_mask_video: str
    prompt: str
    dataset_id: str
    dataset_version: str
    license: str
    license_scope: str
    consent: str


@dataclass
class BuildReport:
    samples: list[SampleRow] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    near_duplicates: list[tuple[str, str]] = field(default_factory=list)


def assess_masks(masks: list[np.ndarray]) -> str | None:
    """Why this clip can't teach the model anything useful, or None. Every rejection is recorded with its reason."""
    coverages = [float(mask.mean()) for mask in masks]
    lost = sum(1 for coverage in coverages if coverage < MIN_COVERAGE)
    if lost / len(masks) > MAX_LOST_FRACTION:
        return "subject_missing_or_too_small"
    if max(coverages) > MAX_COVERAGE:
        return "subject_fills_the_frame"
    return None


def assess_clip(clip: ClipIndex) -> str | None:
    if not clip.masks:
        return "no_mask"
    if len(clip.images) != len(clip.masks):
        return "mask_count_mismatch"
    if len(clip.images) < MIN_SOURCE_FRAMES:
        return "too_short"
    return None


def choose_split(group: str, shares: dict[str, float] = SPLIT_SHARES) -> str:
    """From a hash of the source group: stable between runs, and a whole group always lands together."""
    position = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    total = 0.0
    for name, share in shares.items():
        total += share
        if position <= total:
            return name
    return next(reversed(shares))


def assert_no_leak(rows: list[SampleRow]) -> None:
    splits_by_group: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        splits_by_group[row.source_group].add(row.split)
    leaking = {
        group: sorted(splits) for group, splits in splits_by_group.items() if len(splits) > 1
    }
    if leaking:
        raise SplitLeakError(f"sources appear in more than one split: {leaking}")


def perceptual_hash(frame: np.ndarray) -> int:
    """A difference hash: survives re-encoding and small crops, and two uploads of one video land close together."""
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if bit else "0" for bit in bits), 2)


def find_near_duplicates(
    hashes: dict[str, int], max_bits: int = NEAR_DUPLICATE_BITS
) -> list[tuple[str, str]]:
    names = sorted(hashes)
    return [
        (left, right)
        for index, left in enumerate(names)
        for right in names[index + 1 :]
        if (hashes[left] ^ hashes[right]).bit_count() <= max_bits
    ]


def fixed_eval_set(rows: list[SampleRow], size: int = 8) -> list[str]:
    """Chosen by hash from the test split alone, so it never changes as more data is added elsewhere."""
    test = [row.clip_id for row in rows if row.split == "test"]
    return sorted(test, key=lambda clip_id: hashlib.sha256(clip_id.encode()).hexdigest())[:size]


class ControlFrameMismatch(ValueError):
    """The cut decoded a different number of frames than there are masks for it."""


class DatasetMaskTracker:
    """Uses the dataset's own masks in place of SAM 2. The rest of the pipeline is the one serving uses."""

    revision = "dataset-masks"

    def __init__(self, masks: list[np.ndarray]) -> None:
        self.masks = masks

    def track(self, frames: list[np.ndarray], prompts: list[FramePrompt]) -> list[np.ndarray]:
        if len(frames) != len(self.masks):
            raise ControlFrameMismatch(f"{len(frames)} frames but {len(self.masks)} masks")
        return self.masks


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_sample(
    archive: zipfile.ZipFile,
    clip: ClipIndex,
    entry: DatasetEntry,
    out: Path,
    pose: PoseEstimator,
    split: str,
    caption: str = "a person dancing",
) -> SampleRow | Rejection:
    reason = assess_clip(clip)
    if reason:
        return Rejection(clip.clip_id, reason)
    # Checked on the source, since every frame is scaled to the canvas once it's read.
    if min(first_frame(archive, clip).shape[:2]) < MIN_SIDE:
        return Rejection(clip.clip_id, "too_low_resolution")

    work = out / "_extract" / clip.clip_id
    extracted = extract_clip(archive, clip, work)
    width, height = CANVAS["width"], CANVAS["height"]
    frames = read_frames(extracted["video"], width, height)
    masks = [
        (frame[:, :, 0] > 127).astype(np.uint8)
        for frame in read_frames(extracted["mask"], width, height)
    ]
    reason = assess_masks(masks)
    if reason:
        return Rejection(clip.clip_id, reason)

    # The reference sits before the target range, far enough away to be a genuinely different pose.
    reference_frame = 0
    target_start = reference_frame + REFERENCE_GAP
    target = frames[target_start : target_start + TARGET_FRAMES]
    control_range = target_start + CONTROL_OFFSET, target_start + TARGET_FRAMES

    sample_dir = out / "samples" / clip.clip_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    video_path = sample_dir / "video.mp4"
    write_video([frames[reference_frame], *target], video_path, DATASET_FPS, crf=14)

    context_dir = sample_dir / "_controls"
    settings = {**CANVAS, "processing_fps": DATASET_FPS, "mask_expand_pixels": 9}
    start_seconds = control_range[0] / DATASET_FPS
    inputs = {
        # The dataset's masks choose the dancer, so this only has to be a mark inside the range.
        "annotations": {
            "box": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0, "frame": control_range[0]}
        },
        "frame_index": control_range[0],
        "trim_start_seconds": start_seconds,
        "trim_end_seconds": control_range[1] / DATASET_FPS,
    }
    # The dataset's source is cut to the control range, and so must its masks be, or they'd pair with the wrong frames.
    tracker = DatasetMaskTracker(masks[control_range[0] : control_range[1]])
    try:
        run_preprocess(extracted["video"], inputs, settings, JobContext(context_dir), tracker, pose)
    except ControlFrameMismatch:
        # Cutting by time can round to a frame more or less. Losing this clip is better than losing the build.
        return Rejection(clip.clip_id, "control_frame_mismatch")

    names = {
        "animate_pose_video": "src_pose.mp4",
        "animate_face_video": "src_face.mp4",
        "animate_inpaint_video": "src_bg.mp4",
        "animate_mask_video": "src_mask.mp4",
    }
    paths: dict[str, str] = {}
    for key, name in names.items():
        target_path = sample_dir / name
        (context_dir / name).replace(target_path)
        paths[key] = str(target_path.relative_to(out))

    return SampleRow(
        clip_id=clip.clip_id,
        split=split,
        source_group=clip.source_group,
        # The dataset has no person ids. A raw compilation is the narrowest grouping that can't split one person.
        person_id=f"unknown:{clip.source_group}",
        source_sha256=_sha256(extracted["video"]),
        reference_frame=reference_frame,
        target_start_frame=target_start,
        video=str(video_path.relative_to(out)),
        prompt=caption,
        dataset_id=entry.id,
        dataset_version=str(entry.version),
        license=entry.license_name,
        license_scope=entry.review.scope or "unreviewed",
        consent=entry.consent_notes or "",
        **paths,
    )


def first_frame(archive: zipfile.ZipFile, clip: ClipIndex) -> np.ndarray:
    return read_png(archive, clip.images[0], "RGB")


def build_samples(
    archive_path: Path,
    entry: DatasetEntry,
    out: Path,
    pose: PoseEstimator,
    limit: int | None = None,
    clip_ids: list[str] | None = None,
) -> BuildReport:
    """Refuses a dataset without a review that covers research training. Never promoted beyond its licence."""
    assert_training_allowed(entry, Purpose.RESEARCH)
    out.mkdir(parents=True, exist_ok=True)
    report = BuildReport()
    with zipfile.ZipFile(archive_path) as archive:
        clips = index_clips(archive)
        chosen = [clips[clip_id] for clip_id in clip_ids] if clip_ids else list(clips.values())

        # Duplicates across groups would leak the same video into train and test, so one of each pair is dropped.
        hashes = {clip.clip_id: perceptual_hash(first_frame(archive, clip)) for clip in chosen}
        groups = {clip.clip_id: clip.source_group for clip in chosen}
        pairs = [
            pair for pair in find_near_duplicates(hashes) if groups[pair[0]] != groups[pair[1]]
        ]
        report.near_duplicates = pairs
        dropped = {right for _, right in pairs}

        built = 0
        for clip in chosen:
            if limit is not None and built >= limit:
                break
            if clip.clip_id in dropped:
                report.rejections.append(
                    Rejection(clip.clip_id, "near_duplicate_of_another_source")
                )
                continue
            outcome = build_sample(archive, clip, entry, out, pose, choose_split(clip.source_group))
            if isinstance(outcome, Rejection):
                report.rejections.append(outcome)
            else:
                report.samples.append(outcome)
                built += 1

    assert_no_leak(report.samples)
    write_outputs(report, entry, out)
    return report


def write_outputs(report: BuildReport, entry: DatasetEntry, out: Path) -> None:
    rows = [asdict(sample) for sample in report.samples]
    trainer_keys = [
        "video",
        "animate_pose_video",
        "animate_face_video",
        "animate_inpaint_video",
        "animate_mask_video",
        "prompt",
    ]
    for split in SPLIT_SHARES:
        with (out / f"metadata_{split}.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, trainer_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(row for row in rows if row["split"] == split)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "dataset": {
            "id": entry.id,
            "version": entry.version,
            "license": entry.license_name,
            "scope": entry.review.scope,
        },
        "commercial_use_ok": entry.commercial_use_ok,
        "video_frames": VIDEO_FRAMES,
        "control_frames": CONTROL_FRAMES,
        "control_offset_note": "control k shows target frame k+3. Inferred from the trainer's code, unverified on a real run.",
        "fixed_eval_set": fixed_eval_set(report.samples),
        "samples": rows,
        "rejections": [asdict(rejection) for rejection in report.rejections],
        "rejection_counts": dict(Counter(rejection.reason for rejection in report.rejections)),
        "near_duplicates": report.near_duplicates,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "DATASET_CARD.md").write_text(dataset_card(report, entry))


def dataset_card(report: BuildReport, entry: DatasetEntry) -> str:
    counts = Counter(sample.split for sample in report.samples)
    reasons = Counter(rejection.reason for rejection in report.rejections)
    lines = [
        f"# {entry.name}",
        "",
        f"- Version: {entry.version}",
        f"- Licence: {entry.license_name} ({entry.license_url})",
        f"- Review: {entry.review.scope}, by {entry.review.reviewer} on {entry.review.review_date}",
        f"- Commercial use: {'allowed' if entry.commercial_use_ok else 'not allowed'}",
        f"- Attribution: {entry.attribution}",
        f"- Consent: {entry.consent_notes}",
        "",
        "## Licence terms",
        "",
        str(entry.license_notes),
        "",
        "## This build",
        "",
        *(f"- {split}: {counts.get(split, 0)} samples" for split in SPLIT_SHARES),
        *(f"- Rejected, {reason}: {count}" for reason, count in sorted(reasons.items())),
        f"- Near-duplicate pairs across sources: {len(report.near_duplicates)}",
        "",
        "## Known limits",
        "",
        "- The dataset has no person ids. Splits group by raw compilation, which can't separate one person's clips only",
        "  if that person appears in one compilation. The same person in two compilations could still leak.",
        "- Trained adapters are research-only and can't become a production default.",
    ]
    return "\n".join(lines) + "\n"
