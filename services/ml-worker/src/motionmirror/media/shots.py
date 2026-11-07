"""Find hard cuts. A tracker must never follow a person across unrelated shots."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from motionmirror.media.ffmpeg import MediaError, file_input

# Measured on the TikTok dataset: 0.4 missed one of six real joins, and 0.3 caught them all with no false alarms on
# 26 continuous clips. A false alarm costs a trim, while a miss tracks silently across a cut. See docs/DATASETS.md.
SCENE_THRESHOLD = 0.3
_TIME = re.compile(r"pts_time:([0-9.]+)")


def detect_cuts(path: Path, threshold: float = SCENE_THRESHOLD, timeout: int = 300) -> list[float]:
    """Timestamps, in seconds, where the picture changes abruptly. Raises rather than guessing there are none."""
    command = [
        "ffmpeg", "-hide_banner", "-nostdin", "-protocol_whitelist", "file,pipe", "-i", file_input(path),
        "-map", "0:v:0", "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-",
    ]  # fmt: skip
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise MediaError("cut detection ran past its limit") from error
    if done.returncode != 0:
        raise MediaError("cut detection failed")
    return [round(float(value), 3) for value in _TIME.findall(done.stderr)]
