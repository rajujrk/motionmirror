"""A labelled stand-in for segmentation, so the workflow can be exercised without any model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from motionmirror.backends.base import JobContext
from motionmirror.backends.mock import MOCK_LABEL, make_banner
from motionmirror.media.ffmpeg import file_input, run_ffmpeg
from motionmirror.media.probe import probe

BANNER_TEXT = "MOCK MASK / NOT REAL SEGMENTATION"
_DEFAULT_BOX = (0.3, 0.1, 0.4, 0.8)


def selection_box(annotations: dict[str, Any]) -> tuple[float, float, float, float]:
    """A normalized (x, y, width, height) box around the user's selection."""
    box = annotations.get("box")
    if isinstance(box, dict):
        return float(box["x"]), float(box["y"]), float(box["width"]), float(box["height"])
    positives = [p for p in annotations.get("points", []) if p.get("positive", True)]
    if positives:
        x, y = float(positives[0]["x"]), float(positives[0]["y"])
        left, top = max(x - 0.15, 0.0), max(y - 0.25, 0.0)
        return left, top, min(0.3, 1 - left), min(0.5, 1 - top)
    return _DEFAULT_BOX


def run_mock_preprocess(source: Path, inputs: dict[str, Any], context: JobContext) -> dict[str, Any]:
    context.progress("decode")
    info = probe(source)
    context.progress("segment")
    x, y, width, height = selection_box(inputs.get("annotations") or {})
    banner = make_banner(info.display_width, info.display_height, context.workspace / "mask_banner.png", BANNER_TEXT)
    preview = context.workspace / "preview.mp4"
    box = f"x=iw*{x:.4f}:y=ih*{y:.4f}:w=iw*{width:.4f}:h=ih*{height:.4f}:color=lime@0.45:t=fill"
    args = ["-i", file_input(source), "-i", file_input(banner)]
    start = inputs.get("trim_start_seconds")
    end = inputs.get("trim_end_seconds")
    if start is not None:
        args = ["-ss", f"{float(start):.6f}", *args]
    if end is not None:
        args += ["-t", f"{float(end) - float(start or 0):.6f}"]
    run_ffmpeg(
        [
            *args,
            "-filter_complex", f"[0:v:0]drawbox={box}[boxed];[boxed][1:v:0]overlay=0:0,format=yuv420p[out]",
            "-map", "[out]", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-movflags", "+faststart",
            str(preview),
        ]
    )  # fmt: skip
    context.progress("extract_motion")
    manifest = context.workspace / "preprocess_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "label": MOCK_LABEL,
                "output_label": "mock",
                "tool_revision": "mock-1",
                "selection_revision": inputs.get("selection_revision"),
                "mask_box": {"x": x, "y": y, "width": width, "height": height},
                "note": "Box drawn around the selection. No segmentation or pose model ran.",
            },
            indent=2,
        )
    )
    return {
        "outputs": [
            {"name": "preview.mp4", "path": str(preview), "content_type": "video/mp4"},
            {"name": "preprocess_manifest.json", "path": str(manifest), "content_type": "application/json"},
        ],
        "result": {"tool_revision": "mock-1", "output_label": "mock"},
        "effective_settings": {},
    }
