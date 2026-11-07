"""The TikTok Dance archive: clips as PNG frames with masks. Treated as untrusted input."""

from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np
from motionmirror.media.frames import write_video
from PIL import Image

_FRAME = re.compile(r"^TikTok_dataset/TikTok_dataset/(\d{5})/(images|masks)/(\d{4})\.png$")
_SOURCE_VIDEO = re.compile(r"^TikTok_Raw_Videos/TikTok_Raw_Videos/seq_(\d{5})_(\d{5})/")
MAX_FRAME_PIXELS = 8_000_000
DATASET_FPS = 30


class UnsafeArchive(ValueError):
    """An archive entry tried to escape, or isn't what it claims to be."""


@dataclass(frozen=True)
class ClipIndex:
    clip_id: str
    images: tuple[str, ...]
    masks: tuple[str, ...]
    source_group: str


def check_member(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise UnsafeArchive(f"unsafe archive path: {name!r}")


def index_clips(archive: zipfile.ZipFile) -> dict[str, ClipIndex]:
    """Find every clip. Clips from one raw compilation share a source_group, which keeps splits from leaking."""
    images: dict[str, list[str]] = defaultdict(list)
    masks: dict[str, list[str]] = defaultdict(list)
    groups: list[tuple[int, int]] = []
    for name in archive.namelist():
        check_member(name)
        frame = _FRAME.match(name)
        if frame:
            (images if frame.group(2) == "images" else masks)[frame.group(1)].append(name)
            continue
        source = _SOURCE_VIDEO.match(name)
        if source:
            span = (int(source.group(1)), int(source.group(2)))
            if span not in groups:
                groups.append(span)

    def group_of(clip_id: str) -> str:
        number = int(clip_id)
        for first, last in groups:
            if first <= number <= last:
                return f"seq_{first:05d}_{last:05d}"
        return f"clip_{clip_id}"

    return {
        clip_id: ClipIndex(
            clip_id,
            tuple(sorted(images[clip_id])),
            tuple(sorted(masks.get(clip_id, []))),
            group_of(clip_id),
        )
        for clip_id in sorted(images)
    }


def read_png(archive: zipfile.ZipFile, name: str, mode: str) -> np.ndarray:
    check_member(name)
    with Image.open(io.BytesIO(archive.read(name))) as image:
        if image.format != "PNG":
            raise UnsafeArchive(f"{name} isn't a PNG")
        if image.width * image.height > MAX_FRAME_PIXELS:
            raise UnsafeArchive(f"{name} is too large")
        return np.asarray(image.convert(mode))


def extract_clip(
    archive: zipfile.ZipFile, clip: ClipIndex, output: Path, max_frames: int | None = None
) -> dict[str, Path]:
    """Encode a clip and its dataset mask to MP4. Nothing from the archive is written under its own name."""
    output.mkdir(parents=True, exist_ok=True)
    names = clip.images[:max_frames] if max_frames else clip.images
    video = output / "video.mp4"
    write_video(
        (_even(read_png(archive, name, "RGB")) for name in names), video, DATASET_FPS, crf=14
    )
    result = {"video": video}
    if clip.masks:
        masks = clip.masks[:max_frames] if max_frames else clip.masks
        mask = output / "dataset_mask.mp4"
        write_video(
            (
                _even(np.repeat(read_png(archive, name, "L")[:, :, None], 3, axis=2))
                for name in masks
            ),
            mask,
            DATASET_FPS,
            crf=10,
        )
        result["mask"] = mask
    return result


def _even(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    return frame[: height // 2 * 2, : width // 2 * 2]
