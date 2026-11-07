"""FFmpeg and ffprobe with argument arrays, bounded time and no network protocols."""

from __future__ import annotations

import subprocess
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 1800
_SAFE_PROTOCOLS = "file,pipe"


class MediaError(RuntimeError):
    """FFmpeg failed or the file isn't usable media."""


def file_input(path: Path | str) -> str:
    """Force the file protocol, so a name like `http:x` can never be read as a URL."""
    return f"file:{Path(path).resolve()}"


def _restrict_inputs(args: list[str]) -> list[str]:
    restricted: list[str] = []
    for arg in args:
        if arg == "-i":
            restricted += ["-protocol_whitelist", _SAFE_PROTOCOLS]
        restricted.append(arg)
    return restricted


def run_ffmpeg(args: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *_restrict_inputs(args)]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise MediaError("ffmpeg timed out") from error
    if done.returncode != 0:
        raise MediaError(f"ffmpeg failed: {done.stderr[-500:].strip()}")


def run_ffprobe(path: Path | str, timeout: int = 60) -> str:
    command = [
        "ffprobe", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
        "-print_format", "json", "-show_format", "-show_streams", file_input(path),
    ]  # fmt: skip
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise MediaError("ffprobe timed out") from error
    if done.returncode != 0:
        raise MediaError(f"not readable media: {done.stderr[-300:].strip()}")
    return done.stdout
