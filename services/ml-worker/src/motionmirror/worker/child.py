"""One attempt, run in its own process. It sees only local files and has no credentials or network calls.

The parent downloads inputs, heartbeats, and uploads outputs. It can terminate this process at any time,
so a stuck model can't stop heartbeats or cancellation.
"""

from __future__ import annotations

import json
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from motionmirror.backends.base import GenerationRequest, JobContext
from motionmirror.backends.mock import sha256_file
from motionmirror.backends.registry import create_backend
from motionmirror.media.validation import RejectedMedia, validate_image, validate_video

FAILURE_CODES = {"OutOfMemoryError": "CUDA_OOM"}


class AttemptFailure(RuntimeError):
    def __init__(self, code: str, detail: str, retryable: bool = False) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retryable = retryable


class PipeContext(JobContext):
    """Progress goes to the parent as JSON lines on stdout."""

    def progress(
        self, stage: str, current: int | None = None, total: int | None = None, unit: str | None = None
    ) -> None:
        print(json.dumps({"stage": stage, "current": current, "total": total, "unit": unit}), flush=True)


def run_validate(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    inputs = spec["request"]["inputs"]
    path = Path(spec["input_paths"]["asset"])
    context.progress("validate")
    try:
        if inputs["role"] == "DRIVING_VIDEO":
            media = validate_video(path, float(inputs["max_seconds"]))
            detected = f"video/{media['video_codec']}"
            outputs: list[dict[str, str]] = []
            digest = sha256_file(path)
        else:
            normalized = context.workspace / "normalized"
            media = validate_image(path, normalized)
            detected = "image/png"
            digest = sha256_file(normalized)
            outputs = [{"name": "normalized", "path": str(normalized), "content_type": "image/png"}]
    except RejectedMedia as rejected:
        raise AttemptFailure("INVALID_MEDIA", str(rejected)) from rejected
    return {
        "outputs": outputs,
        "result": {"sha256": digest, "detected_type": detected, "media": media},
        "effective_settings": {},
    }


def run_preprocess(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    source = Path(spec["input_paths"]["source"])
    inputs = spec["request"]["inputs"]
    if spec["request"]["backend_id"] == "mock":
        from motionmirror.preprocessing.mock import run_mock_preprocess

        return run_mock_preprocess(source, inputs, context)
    from motionmirror.preprocessing.pipeline import run_preprocess as run_real

    return run_real(source, inputs, spec["request"]["settings"], context)


def run_generate(spec: dict[str, Any], context: JobContext) -> dict[str, Any]:
    request_data = spec["request"]
    paths = spec["input_paths"]
    controls = {
        name.removeprefix("preprocess:"): Path(path) for name, path in paths.items() if name.startswith("preprocess:")
    }
    request = GenerationRequest(
        job_id=request_data["job_id"],
        kind=request_data["kind"],
        settings=request_data["settings"],
        inputs=request_data["inputs"],
        source_path=Path(paths["source"]),
        primary_reference_path=Path(paths["primary_reference"]) if "primary_reference" in paths else None,
        control_paths=controls,
    )
    backend = create_backend(request_data["backend_id"])
    verdict = backend.validate(request)
    if not verdict.ok:
        raise AttemptFailure(verdict.code or "INVALID_REQUEST", verdict.detail or "Unsupported request")
    try:
        result = backend.generate(backend.prepare(request), context)
    finally:
        backend.release()
    return {
        "outputs": [
            {"name": "result.mp4", "path": str(result.video_path), "content_type": "video/mp4"},
            {"name": "manifest.json", "path": str(result.manifest_path), "content_type": "application/json"},
        ],
        "result": {"capabilities": asdict(backend.capabilities())},
        "effective_settings": result.effective_settings,
    }


HANDLERS = {"VALIDATE": run_validate, "PREPROCESS": run_preprocess, "PREVIEW": run_generate, "FULL": run_generate}


def classify(error: BaseException) -> tuple[str, str, bool]:
    """Separate failure kinds, so an out-of-memory never turns into a silent quality drop."""
    if isinstance(error, AttemptFailure):
        return error.code, error.detail, error.retryable
    name = type(error).__name__
    if name in FAILURE_CODES:
        return FAILURE_CODES[name], "The model ran out of GPU memory at these settings", False
    return "INTERNAL_ERROR", f"{name} while processing", True


def main(spec_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text())
    workspace = Path(spec["workspace"])
    context = PipeContext(workspace)
    result_path = workspace / "attempt_result.json"
    try:
        payload = {"ok": True, **HANDLERS[spec["request"]["kind"]](spec, context)}
    except BaseException as error:
        code, detail, retryable = classify(error)
        traceback.print_exc(file=sys.stderr)
        payload = {"ok": False, "code": code, "detail": detail, "retryable": retryable}
    result_path.write_text(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
