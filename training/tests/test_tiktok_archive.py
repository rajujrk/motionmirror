from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest
from motionmirror.media.frames import read_frames
from PIL import Image

from motionmirror_training.datasets.tiktok import (
    UnsafeArchive,
    check_member,
    extract_clip,
    index_clips,
)


def png(color: int, size: tuple[int, int] = (64, 96), mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color if mode == "L" else (color, color, color)).save(buffer, "PNG")
    return buffer.getvalue()


def make_archive(path: Path, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("License.pdf", b"terms")
        archive.writestr(
            "TikTok_Raw_Videos/TikTok_Raw_Videos/seq_00001_00002/video_link.txt", b"link"
        )
        for clip in ("00001", "00002", "00009"):
            for frame in range(1, 4):
                archive.writestr(
                    f"TikTok_dataset/TikTok_dataset/{clip}/images/{frame:04d}.png", png(100)
                )
                archive.writestr(
                    f"TikTok_dataset/TikTok_dataset/{clip}/masks/{frame:04d}.png",
                    png(255, mode="L"),
                )
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def test_clips_are_indexed_with_frames_and_masks(tmp_path: Path) -> None:
    with zipfile.ZipFile(make_archive(tmp_path / "a.zip")) as archive:
        clips = index_clips(archive)
    assert set(clips) == {"00001", "00002", "00009"}
    assert len(clips["00001"].images) == 3
    assert len(clips["00001"].masks) == 3


def test_clips_from_one_compilation_share_a_group_so_splits_cannot_leak(tmp_path: Path) -> None:
    with zipfile.ZipFile(make_archive(tmp_path / "a.zip")) as archive:
        clips = index_clips(archive)
    assert clips["00001"].source_group == clips["00002"].source_group
    assert clips["00009"].source_group != clips["00001"].source_group


@pytest.mark.parametrize("name", ["../../etc/passwd", "/etc/passwd", "a/../../b.png"])
def test_paths_that_escape_are_refused(name: str) -> None:
    with pytest.raises(UnsafeArchive):
        check_member(name)


def test_an_archive_with_a_hostile_path_is_refused_when_indexed(tmp_path: Path) -> None:
    path = make_archive(tmp_path / "a.zip", {"../../evil.sh": b"#!/bin/sh"})
    with zipfile.ZipFile(path) as archive, pytest.raises(UnsafeArchive):
        index_clips(archive)


def test_a_frame_that_is_not_a_png_is_refused(tmp_path: Path) -> None:
    name = "TikTok_dataset/TikTok_dataset/00003/images/0001.png"
    path = make_archive(tmp_path / "a.zip", {name: b"<script>not an image</script>"})
    with zipfile.ZipFile(path) as archive:
        clip = index_clips(archive)["00003"]
        with pytest.raises(Exception):  # noqa: B017 - any decode refusal is correct here
            extract_clip(archive, clip, tmp_path / "out")


def test_a_clip_extracts_to_video_and_mask(tmp_path: Path) -> None:
    with zipfile.ZipFile(make_archive(tmp_path / "a.zip")) as archive:
        result = extract_clip(archive, index_clips(archive)["00001"], tmp_path / "out")
    frames = read_frames(result["video"], 64, 96)
    assert len(frames) == 3
    assert np.abs(frames[0].astype(int) - 100).mean() < 5
    assert read_frames(result["mask"], 64, 96)[0].mean() > 240
