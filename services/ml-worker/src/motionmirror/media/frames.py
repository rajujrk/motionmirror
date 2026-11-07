"""Frames through FFmpeg pipes. Resampling is by timestamp, so variable-frame-rate clips keep their timing."""

from __future__ import annotations

import itertools
import subprocess
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import IO

import numpy as np

from motionmirror.media.ffmpeg import MediaError, file_input, run_ffmpeg

AI_EDITED_TAG = "AI-edited with MotionMirror"


def fit_filter(width: int, height: int) -> str:
    """Keep the aspect ratio and pad to the model canvas. coords.ts in the web app maps clicks the same way."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=bicubic,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
    )


def content_rect(source_width: int, source_height: int, width: int, height: int) -> tuple[int, int, int, int]:
    """The (x, y, width, height) of the picture inside the padded canvas, with even sides for H.264."""
    scale = min(width / source_width, height / source_height)
    content_width = int(source_width * scale) // 2 * 2
    content_height = int(source_height * scale) // 2 * 2
    return (width - content_width) // 2, (height - content_height) // 2, content_width, content_height


def _tail(errors: IO[bytes]) -> str:
    errors.seek(0)
    return errors.read()[-300:].decode(errors="replace")


def iter_frames(
    path: Path,
    width: int,
    height: int,
    fps: int | None = None,
    start: float | None = None,
    duration: float | None = None,
) -> Iterator[np.ndarray]:
    """Decoded RGB frames fitted to width by height. FFmpeg has already applied any rotation.

    stderr goes to a file, not a pipe: a damaged clip can log enough to fill a pipe nobody reads, and FFmpeg would
    then block forever with the job still holding its lease.
    """
    filters = ([f"fps={fps}"] if fps else []) + [fit_filter(width, height)]
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start is not None:
        args += ["-ss", f"{start:.6f}"]
    if duration is not None:
        args += ["-t", f"{duration:.6f}"]
    args += ["-protocol_whitelist", "file,pipe", "-i", file_input(path), "-map", "0:v:0"]
    args += ["-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    size = width * height * 3
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors)
        assert process.stdout is not None
        finished = False
        try:
            while True:
                data = process.stdout.read(size)
                if len(data) < size:
                    finished = True
                    break
                yield np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
        finally:
            if not finished:
                process.kill()
            process.stdout.close()
            code = process.wait()
        # Without this a decode failure looks like a clip that simply ended early.
        if code != 0:
            raise MediaError(f"decoding failed: {_tail(errors)}")


def read_frames(path: Path, width: int, height: int, **kwargs: object) -> list[np.ndarray]:
    return list(iter_frames(path, width, height, **kwargs))  # type: ignore[arg-type]


def write_video(frames: Iterable[np.ndarray], path: Path, fps: int, crf: int = 16) -> int:
    """Encode RGB frames to a broadly playable H.264 MP4. Returns the frame count."""
    iterator = iter(frames)
    first = next(iterator, None)
    if first is None:
        raise MediaError("no frames to encode")
    height, width = first.shape[:2]
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-protocol_whitelist", "file,pipe",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(path),
    ]  # fmt: skip
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
        assert process.stdin is not None
        count = 0
        broken = False
        try:
            for frame in itertools.chain([first], iterator):
                process.stdin.write(frame.tobytes())
                count += 1
        except BrokenPipeError:
            broken = True
        finally:
            try:
                process.stdin.close()
            except BrokenPipeError:
                broken = True
            code = process.wait()
        if broken or code != 0:
            raise MediaError(f"encoding failed: {_tail(errors)}")
    return count


def finalize_output(
    video: Path, source: Path, output: Path, start: float | None, duration: float, keep_audio: bool
) -> None:
    """The file people download: the source's first audio track cut to the same range, and marked as AI-edited."""
    args = ["-i", file_input(video)]
    if keep_audio:
        if start is not None:
            args += ["-ss", f"{start:.6f}"]
        args += ["-t", f"{duration:.6f}", "-i", file_input(source)]
        args += ["-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-b:a", "192k"]
    else:
        args += ["-map", "0:v:0", "-an"]
    args += ["-c:v", "copy", "-map_metadata", "-1", "-metadata", f"comment={AI_EDITED_TAG}"]
    args += ["-movflags", "+faststart", str(output)]
    run_ffmpeg(args)
