"""Generated media fixtures. Synthetic only, so no likeness or licence questions arise."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def run_ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def clip_with_audio(fixtures_dir: Path) -> Path:
    path = fixtures_dir / "with_audio.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def silent_clip(fixtures_dir: Path) -> Path:
    path = fixtures_dir / "silent.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def rotated_clip(clip_with_audio: Path, fixtures_dir: Path) -> Path:
    path = fixtures_dir / "rotated.mp4"
    run_ffmpeg("-display_rotation", "90", "-i", str(clip_with_audio), "-c", "copy", str(path))
    return path


@pytest.fixture(scope="session")
def vfr_clip(fixtures_dir: Path) -> Path:
    """30 fps for one second, then a one-second gap in timestamps."""
    path = fixtures_dir / "vfr.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
        "-vf", "setpts='if(lt(N,30),N/30/TB,(N+30)/30/TB)'",
        "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def multi_audio_clip(fixtures_dir: Path) -> Path:
    path = fixtures_dir / "multi_audio.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
        "-map", "0", "-map", "1", "-map", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path
