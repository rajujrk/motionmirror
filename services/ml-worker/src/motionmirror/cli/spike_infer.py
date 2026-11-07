"""One clip through the real path, without the job system: make the controls, attempt a replacement, record it all.

This is the GPU benchmark. It also runs on a Mac with --preprocess-only to check real masks and poses by eye.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from PIL import Image

from motionmirror.backends.base import GenerationRequest, JobContext, VideoReplacementBackend
from motionmirror.media.frames import read_frames
from motionmirror.media.probe import MediaInfo, probe
from motionmirror.preprocessing.pipeline import PoseEstimator, Tracker, run_preprocess
from motionmirror.worker.failures import AttemptFailure

REPO_ROOT = Path(__file__).resolve().parents[5]
PRESETS_DIR = Path(os.environ.get("MM_PRESETS_DIR", REPO_ROOT / "configs" / "presets"))
SHEET_COLUMNS = 6
SHEET_ROWS = ("preview.mp4", "src_mask.mp4", "src_pose.mp4", "src_bg.mp4", "src_face.mp4", "result.mp4")
CELL_HEIGHT = 256


def load_settings(preset: str, info: MediaInfo) -> dict[str, Any]:
    """The same orientation rule as the API, so a landscape clip isn't squeezed into a portrait canvas."""
    definition = yaml.safe_load((PRESETS_DIR / f"{preset}.v1.yaml").read_text())
    settings = dict(definition["settings"])
    portrait = info.display_height >= info.display_width
    height, width = settings["height"], settings["width"]
    if portrait != (height > width) and height != width:
        settings["height"], settings["width"] = width, height
    settings["preview_seconds"] = definition.get("preview_seconds")
    return settings


def annotations(marks: list[str], frame: int) -> dict[str, Any]:
    """Each mark is x,y in 0..1. A leading - marks someone to leave out."""
    points = []
    for mark in marks:
        positive = not mark.startswith("-")
        x, y = (float(value) for value in mark.lstrip("+-").split(","))
        points.append({"x": x, "y": y, "positive": positive, "frame": frame})
    return {"points": points}


def peak_host_memory_gb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(value / (1024**3 if sys.platform == "darwin" else 1024**2), 2)


def directory_gb(path: Path) -> float:
    return round(sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) / 1024**3, 3)


def contact_sheet(directory: Path, output: Path, columns: int = SHEET_COLUMNS) -> Path | None:
    """One glance at several moments: does the mask stay on the dancer, does the pose follow, is the face right?"""
    rows = []
    for name in SHEET_ROWS:
        path = directory / name
        if not path.exists():
            continue
        info = probe(path)
        frames = read_frames(path, info.display_width, info.display_height)
        picks = np.linspace(0, len(frames) - 1, columns).astype(int)
        cells = []
        for index in picks:
            image = Image.fromarray(frames[index])
            cells.append(image.resize((image.width * CELL_HEIGHT // image.height, CELL_HEIGHT)))
        rows.append(cells)
    if not rows:
        return None
    width = max(sum(cell.width for cell in row) for row in rows)
    sheet = Image.new("RGB", (width, CELL_HEIGHT * len(rows)), (255, 255, 255))
    for row_index, row in enumerate(rows):
        x = 0
        for cell in row:
            sheet.paste(cell, (x, row_index * CELL_HEIGHT))
            x += cell.width
    sheet.save(output)
    return output


def run(
    source: Path,
    reference: Path | None,
    marks: list[str],
    out: Path,
    preset: str = "preview",
    frame: int = 0,
    trim: tuple[float | None, float | None] = (None, None),
    preprocess_only: bool = False,
    tracker: Tracker | None = None,
    pose: PoseEstimator | None = None,
    backend: VideoReplacementBackend | None = None,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    info = probe(source)
    settings = load_settings(preset, info)
    inputs = {
        "annotations": annotations(marks, frame),
        "frame_index": frame,
        "trim_start_seconds": trim[0],
        "trim_end_seconds": trim[1],
        "preview_seconds": settings.pop("preview_seconds") if preset == "preview" else None,
    }
    settings.pop("preview_seconds", None)
    report: dict[str, Any] = {
        "preset": preset,
        "settings": settings,
        "source": {"width": info.display_width, "height": info.display_height, "seconds": info.duration_seconds},
        "host": {"machine": platform.machine(), "system": platform.system(), "python": platform.python_version()},
        "device_profile": os.environ.get("MM_DEVICE_PROFILE", "cpu"),
        "generation": {"attempted": False},
    }

    started = time.monotonic()
    work = out / "preprocess"
    outcome = run_preprocess(source, inputs, settings, JobContext(work), tracker, pose)
    report["preprocess"] = {
        "seconds": round(time.monotonic() - started, 1),
        "tool_revision": outcome["result"]["tool_revision"],
        **outcome["effective_settings"],
        "peak_host_memory_gb": peak_host_memory_gb(),
        "scratch_gb": directory_gb(work),
    }

    if not preprocess_only:
        report["generation"] = _generate(source, reference, inputs, settings, work, out, backend)

    sheet = contact_sheet(work, out / "contact_sheet.png")
    report["contact_sheet"] = str(sheet) if sheet else None
    report["peak_host_memory_gb"] = peak_host_memory_gb()
    (out / "benchmark.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def _generate(
    source: Path,
    reference: Path | None,
    inputs: dict[str, Any],
    settings: dict[str, Any],
    work: Path,
    out: Path,
    backend: VideoReplacementBackend | None,
) -> dict[str, Any]:
    """A failure here is a result worth keeping, especially an out-of-memory, so it's recorded rather than raised."""
    if reference is None:
        return {"attempted": False, "reason": "no reference photo given"}
    if backend is None:
        from motionmirror.backends.wan_animate import WanAnimateReplacementBackend

        backend = WanAnimateReplacementBackend.from_environment()
    controls = {name: work / name for name in ("src_pose.mp4", "src_face.mp4", "src_bg.mp4", "src_mask.mp4")}
    request = GenerationRequest(
        job_id="benchmark",
        kind="PREVIEW" if inputs.get("preview_seconds") else "FULL",
        settings=settings,
        inputs=inputs,
        source_path=source,
        primary_reference_path=reference,
        control_paths=controls,
    )
    started = time.monotonic()
    try:
        verdict = backend.validate(request)
        if not verdict.ok:
            return {"attempted": True, "ok": False, "code": verdict.code, "detail": verdict.detail}
        result = backend.generate(backend.prepare(request), JobContext(out / "generate"))
    except Exception as error:
        code = error.code if isinstance(error, AttemptFailure) else type(error).__name__
        return {
            "attempted": True,
            "ok": False,
            "code": code,
            "detail": str(error)[:500],
            "seconds": round(time.monotonic() - started, 1),
            "trace": traceback.format_exc(limit=4)[-1500:],
        }
    finally:
        backend.release()
    shutil.copy(result.video_path, work / "result.mp4")
    manifest = json.loads(result.manifest_path.read_text())
    return {
        "attempted": True,
        "ok": True,
        "seconds": round(time.monotonic() - started, 1),
        "video": str(result.video_path),
        "output_label": manifest["output_label"],
        "may_satisfy_acceptance": manifest["may_satisfy_acceptance"],
        "timings": manifest["timings"],
        "peak_gpu_memory_bytes": manifest["peak_gpu_memory_bytes"],
        "effective_settings": result.effective_settings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mm-spike-infer", description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--mark", action="append", required=True, help="x,y in 0..1 on the dancer. Prefix - to exclude")
    parser.add_argument("--frame", type=int, default=0, help="the source frame the marks are on")
    parser.add_argument("--preset", default="preview", choices=["preview", "standard"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trim-start", type=float)
    parser.add_argument("--trim-end", type=float)
    parser.add_argument("--preprocess-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run(
            args.source,
            args.reference,
            args.mark,
            args.out,
            args.preset,
            args.frame,
            (args.trim_start, args.trim_end),
            args.preprocess_only,
        )
    except AttemptFailure as failure:
        print(f"{failure.code}: {failure.detail}", file=sys.stderr)
        return 2
    print(
        json.dumps({key: report[key] for key in ("preprocess", "generation", "contact_sheet")}, indent=2, default=str)
    )
    return 0 if report["generation"].get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
