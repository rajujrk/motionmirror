"""The evaluation gate: base against adapter, on fixed clips and seeds, through the real worker backend.

Metrics can't prove identity, so an adapter only counts once a person has been through the checklist. Only a CUDA run
can be promotion evidence, because numerics differ across devices.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from motionmirror.backends.base import (
    AdapterInput,
    GenerationRequest,
    JobContext,
    VideoReplacementBackend,
)
from motionmirror.media.frames import FrameSource
from motionmirror.media.probe import probe
from PIL import Image

from motionmirror_training.datasets.samples import DATASET_FPS, fixed_eval_set

# Control k shows video frame k + 4, so the original footage for a composite starts there.
CONTROLS_START_SECONDS = 4 / DATASET_FPS
DEFAULT_SEEDS = (42, 1234)
REGRESSION_SIZE = 4
SHEET_COLUMNS = 6
CELL_HEIGHT = 256
# Section 7 of the brief.
CHECKLIST = (
    "likeness",
    "outfit_continuity",
    "motion_alignment",
    "spins_and_back_views",
    "hands",
    "feet",
    "occlusion",
    "flicker",
    "mask_edges",
    "audio_sync",
)


class EvaluationRefused(RuntimeError):
    """This run can't count as evidence, and the message says why."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def regression_set(rows: list[Any], size: int = REGRESSION_SIZE) -> list[str]:
    """Ordinary clips from the validation split, to catch an adapter making the baseline worse."""
    candidates = [row["clip_id"] for row in rows if row["split"] == "val"]
    return sorted(candidates, key=lambda clip_id: hashlib.sha256(clip_id.encode()).hexdigest())[
        :size
    ]


def load_settings(presets_dir: Path, preset: str, seed: int) -> dict[str, Any]:
    definition = yaml.safe_load((presets_dir / f"{preset}.v1.yaml").read_text())
    return {**definition["settings"], "seed": seed}


def _reference(sample_dir: Path, work: Path, width: int, height: int) -> Path:
    """Frame 0 of a sample's video is the chosen reference, by the trainer's own convention."""
    path = work / "reference.png"
    Image.fromarray(FrameSource(sample_dir / "video.mp4", width, height).slice(0, 1)[0]).save(path)
    return path


def _generate(
    backend: VideoReplacementBackend,
    samples_dir: Path,
    row: dict[str, Any],
    settings: dict[str, Any],
    adapter: AdapterInput | None,
    work: Path,
) -> dict[str, Any]:
    """Records a failure rather than raising, so one out-of-memory doesn't lose the rest of the evaluation."""
    work.mkdir(parents=True, exist_ok=True)
    sample = samples_dir / row["video"]
    controls = {
        "src_pose.mp4": samples_dir / row["animate_pose_video"],
        "src_face.mp4": samples_dir / row["animate_face_video"],
        "src_bg.mp4": samples_dir / row["animate_inpaint_video"],
        "src_mask.mp4": samples_dir / row["animate_mask_video"],
    }
    reference = _reference(sample.parent, work, int(settings["width"]), int(settings["height"]))
    request = GenerationRequest(
        job_id=f"eval-{row['clip_id']}",
        kind="FULL",
        settings=settings,
        inputs={"trim_start_seconds": CONTROLS_START_SECONDS, "primary_reference_asset_id": "eval"},
        source_path=sample,
        primary_reference_path=reference,
        control_paths=controls,
        adapter=adapter,
    )
    started = time.monotonic()
    try:
        verdict = backend.validate(request)
        if not verdict.ok:
            return {"ok": False, "code": verdict.code, "detail": verdict.detail}
        result = backend.generate(backend.prepare(request), JobContext(work))
    except Exception as error:  # noqa: BLE001 - see the docstring
        return {"ok": False, "code": type(error).__name__, "detail": str(error)[:300]}
    return {
        "ok": True,
        "video": str(result.video_path),
        "sha256": _sha256(result.video_path),
        "seconds": round(time.monotonic() - started, 1),
        "manifest": json.loads(result.manifest_path.read_text()),
    }


def _sheet(rows: list[tuple[str, Path]], output: Path) -> Path | None:
    """Base and adapter one above the other, for a person to compare."""
    strips = []
    for _label, path in rows:
        info = probe(path)
        source = FrameSource(path, info.display_width, info.display_height)
        frames = list(source)
        cells = []
        for index in np.linspace(0, len(frames) - 1, SHEET_COLUMNS).astype(int):
            image = Image.fromarray(frames[index])
            cells.append(image.resize((image.width * CELL_HEIGHT // image.height, CELL_HEIGHT)))
        strips.append(cells)
    if not strips:
        return None
    width = max(sum(cell.width for cell in strip) for strip in strips)
    sheet = Image.new("RGB", (width, CELL_HEIGHT * len(strips)), (255, 255, 255))
    for row_index, strip in enumerate(strips):
        x = 0
        for cell in strip:
            sheet.paste(cell, (x, row_index * CELL_HEIGHT))
            x += cell.width
    sheet.save(output)
    return output


def _hardware(device: str) -> dict[str, Any]:
    details: dict[str, Any] = {
        "device_type": device,
        "machine": platform.machine(),
        "system": platform.system(),
    }
    if device == "cuda":
        try:
            gpus = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,driver_version,memory.total",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            details["gpus"] = gpus.stdout.strip().splitlines()
        except OSError:
            details["gpus"] = []
    return details


def evaluate_adapter(
    run_dir: Path,
    samples_dir: Path,
    presets_dir: Path,
    out: Path,
    backend_factory: Callable[[], VideoReplacementBackend],
    device: str,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
    preset: str = "standard",
    not_for_promotion: bool = False,
) -> dict[str, Any]:
    if device != "cuda" and not not_for_promotion:
        raise EvaluationRefused(
            f"This is a {device} machine. Only a CUDA worker can produce promotion evidence. "
            "Pass --not-for-promotion to run it anyway as a check of the path."
        )
    run = json.loads((run_dir / "run_manifest.json").read_text())
    if not run.get("adapter"):
        raise EvaluationRefused(f"{run_dir} has no exported adapter")
    samples = json.loads((samples_dir / "manifest.json").read_text())
    rows = {row["clip_id"]: row for row in samples["samples"]}
    chosen = {
        "evaluation": samples.get("fixed_eval_set") or fixed_eval_set_from(samples["samples"]),
        "regression": regression_set(samples["samples"]),
    }
    if not chosen["evaluation"]:
        raise EvaluationRefused(
            "The sample build has no test-split clips, so there is no fixed evaluation set"
        )

    adapter_path = run_dir / run["adapter"]["path"]
    if _sha256(adapter_path) != run["adapter"]["sha256"]:
        raise EvaluationRefused("The adapter file doesn't match its run manifest")
    adapter = AdapterInput(
        adapter_id="under-evaluation",
        name=run_dir.name,
        path=adapter_path,
        sha256=run["adapter"]["sha256"],
        scale=1.0,
        license_scope=run.get("license_scope") or "unknown",
    )

    out.mkdir(parents=True, exist_ok=True)
    comparisons: list[dict[str, Any]] = []
    for purpose, clip_ids in chosen.items():
        for clip_id in clip_ids:
            for seed in seeds:
                settings = load_settings(presets_dir, preset, seed)
                work = out / purpose / clip_id / str(seed)
                # A fresh backend each time: an adapter attached for one run must never leak into a base run.
                base = _run_one(
                    backend_factory, samples_dir, rows[clip_id], settings, None, work / "base"
                )
                with_adapter = _run_one(
                    backend_factory, samples_dir, rows[clip_id], settings, adapter, work / "adapter"
                )
                entry = {
                    "set": purpose,
                    "clip_id": clip_id,
                    "seed": seed,
                    "base": base,
                    "adapter": with_adapter,
                }
                if base["ok"] and with_adapter["ok"]:
                    sheet = _sheet(
                        [("base", Path(base["video"])), ("adapter", Path(with_adapter["video"]))],
                        work / "contact_sheet.png",
                    )
                    entry["contact_sheet"] = str(sheet)
                    entry["adapter_changed_output"] = base["sha256"] != with_adapter["sha256"]
                comparisons.append(entry)

    complete = all(entry["base"]["ok"] and entry["adapter"]["ok"] for entry in comparisons)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "adapter": {"sha256": run["adapter"]["sha256"], "license_scope": run.get("license_scope")},
        "run_label": run.get("label"),
        "hardware": _hardware(device),
        "promotion_evidence": device == "cuda" and complete and not not_for_promotion,
        "preset": preset,
        "seeds": list(seeds),
        "sets": chosen,
        "comparisons": comparisons,
        "human_review": {"passed": False, "reviewer": None, "checklist": dict.fromkeys(CHECKLIST)},
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    (out / "eval_manifest.json").write_text(json.dumps(manifest, indent=2))
    (out / "MODEL_CARD.md").write_text(model_card(run, samples, manifest))
    return manifest


def _run_one(
    backend_factory: Callable[[], VideoReplacementBackend],
    samples_dir: Path,
    row: dict[str, Any],
    settings: dict[str, Any],
    adapter: AdapterInput | None,
    work: Path,
) -> dict[str, Any]:
    backend = backend_factory()
    try:
        return _generate(backend, samples_dir, row, settings, adapter, work)
    finally:
        backend.release()


def fixed_eval_set_from(rows: list[dict[str, Any]]) -> list[str]:
    from motionmirror_training.datasets.samples import SampleRow

    return fixed_eval_set([SampleRow(**row) for row in rows])


def approve(
    manifest_path: Path, reviewer: str, results: dict[str, bool], notes: str
) -> dict[str, Any]:
    """A person's decision. Every checklist item must be answered, and only real evidence can pass."""
    manifest = json.loads(manifest_path.read_text())
    if not manifest["promotion_evidence"]:
        raise EvaluationRefused("This evaluation isn't promotion evidence, so it can't be approved")
    missing = [item for item in CHECKLIST if item not in results]
    if missing:
        raise EvaluationRefused(
            f"The checklist isn't finished. Still to answer: {', '.join(missing)}"
        )
    unknown = [item for item in results if item not in CHECKLIST]
    if unknown:
        raise EvaluationRefused(f"Not on the checklist: {', '.join(unknown)}")
    if not reviewer.strip():
        raise EvaluationRefused("A review needs a reviewer's name")
    manifest["human_review"] = {
        "passed": all(results.values()),
        "reviewer": reviewer,
        "checklist": results,
        "notes": notes,
        "reviewed_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def model_card(run: dict[str, Any], samples: dict[str, Any], manifest: dict[str, Any]) -> str:
    dataset = samples["dataset"]
    failures = [
        f"- {entry['clip_id']} (seed {entry['seed']}): {side} failed with {entry[side].get('code')}"
        for entry in manifest["comparisons"]
        for side in ("base", "adapter")
        if not entry[side]["ok"]
    ]
    research = dataset.get("scope") != "production"
    lines = [
        f"# Adapter {manifest['adapter']['sha256'][:12]}",
        "",
        "## Intended use",
        "",
        "Research and evaluation only. It was trained on research-only data, and it can't be promoted to production."
        if research
        else "Candidate for production, subject to review and a canary run.",
        "",
        "## Data",
        "",
        f"- {dataset['id']} version {dataset['version']}, licence {dataset['license']}, reviewed scope {dataset.get('scope')}",
        f"- Commercial use allowed: {samples.get('commercial_use_ok')}",
        "",
        "## Training",
        "",
        f"- Run label: {run.get('label')}",
        f"- Trainer: {(run.get('trainer') or {}).get('repo')} at {(run.get('trainer') or {}).get('commit')}",
        f"- Base model: {run.get('base_model')}",
        f"- Rank: {(run.get('lora') or {}).get('rank')}, targets {(run.get('lora') or {}).get('target_modules')}",
        "",
        "## Evaluation",
        "",
        f"- Hardware: {manifest['hardware']['device_type']}",
        f"- Promotion evidence: {manifest['promotion_evidence']}",
        f"- Clips: {manifest['sets']}, seeds {manifest['seeds']}",
        f"- Human review passed: {manifest['human_review']['passed']}",
        "",
        "## Known failures",
        "",
        *(
            failures
            or ["- None recorded in this evaluation. A person still has to check the output."]
        ),
    ]
    return "\n".join(lines) + "\n"
