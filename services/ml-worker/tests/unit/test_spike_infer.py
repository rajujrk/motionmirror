"""The benchmark CLI end to end with fake models, so the record it leaves can be trusted before a GPU is rented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("cv2")

from motionmirror.backends.base import GenerationResult, ValidationResult
from motionmirror.cli.spike_infer import annotations, contact_sheet, load_settings, run
from motionmirror.media.frames import write_video
from motionmirror.media.probe import probe
from motionmirror.preprocessing.mapping import FramePrompt
from tests.unit.test_preprocess_logic import FakePose


class FakeTracker:
    revision = "fake-tracker"

    def track(self, frames: list[np.ndarray], prompts: list[FramePrompt]) -> list[np.ndarray]:
        height, width = frames[0].shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)
        mask[height // 4 : height // 2, width // 4 : width // 2] = 1
        return [mask for _ in frames]


class StubBackend:
    def __init__(self, error: Exception | None = None, verdict: ValidationResult | None = None) -> None:
        self.error = error
        self.verdict = verdict or ValidationResult(True)
        self.released = False
        self.request: Any = None

    def validate(self, request: Any) -> ValidationResult:
        self.request = request
        return self.verdict

    def prepare(self, request: Any) -> Any:
        return request

    def generate(self, inputs: Any, context: Any) -> GenerationResult:
        if self.error:
            raise self.error
        video = context.workspace / "result.mp4"
        write_video([np.full((64, 48, 3), 90, dtype=np.uint8)] * 4, video, 10)
        manifest = context.workspace / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "output_label": "experimental",
                    "may_satisfy_acceptance": False,
                    "timings": {"generate_seconds": 1.0},
                    "peak_gpu_memory_bytes": None,
                }
            )
        )
        return GenerationResult(video, manifest, {"frames": 4})

    def release(self) -> None:
        self.released = True


def benchmark(clip: Path, out: Path, **kwargs: Any) -> dict[str, Any]:
    return run(clip, kwargs.pop("reference", clip), ["0.5,0.5"], out, tracker=FakeTracker(), pose=FakePose(), **kwargs)


def test_marks_keep_their_polarity() -> None:
    points = annotations(["0.25,0.5", "-0.75,0.1"], 12)["points"]
    assert points[0] == {"x": 0.25, "y": 0.5, "positive": True, "frame": 12}
    assert points[1]["positive"] is False


def test_a_landscape_clip_gets_a_landscape_canvas(clip_with_audio: Path) -> None:
    info = probe(clip_with_audio)
    settings = load_settings("preview", info)
    if info.display_width > info.display_height:
        assert settings["width"] > settings["height"]
    else:
        assert settings["height"] > settings["width"]
    assert settings["height"] % 16 == 0
    assert settings["width"] % 16 == 0


def test_preprocess_only_records_the_run_and_attempts_no_generation(clip_with_audio: Path, tmp_path: Path) -> None:
    report = benchmark(clip_with_audio, tmp_path, preprocess_only=True)
    assert report["generation"] == {"attempted": False}
    assert report["preprocess"]["tool_revision"] == "fake-tracker+fake-pose"
    assert report["preprocess"]["scratch_gb"] > 0
    assert json.loads((tmp_path / "benchmark.json").read_text())["preset"] == "preview"
    assert (tmp_path / "contact_sheet.png").exists()


def test_a_generation_is_recorded_and_the_backend_released(clip_with_audio: Path, tmp_path: Path) -> None:
    backend = StubBackend()
    report = benchmark(clip_with_audio, tmp_path, backend=backend)
    assert report["generation"]["ok"] is True
    assert report["generation"]["output_label"] == "experimental"
    assert report["generation"]["may_satisfy_acceptance"] is False
    assert backend.released
    assert set(backend.request.control_paths) == {"src_pose.mp4", "src_face.mp4", "src_bg.mp4", "src_mask.mp4"}
    assert "preview_seconds" not in backend.request.settings, "a run setting mustn't reach the schema-checked settings"


def test_running_out_of_memory_is_a_recorded_result_not_a_crash(clip_with_audio: Path, tmp_path: Path) -> None:
    class OutOfMemoryError(RuntimeError):
        pass

    backend = StubBackend(error=OutOfMemoryError("CUDA out of memory"))
    report = benchmark(clip_with_audio, tmp_path, backend=backend)
    assert report["generation"]["ok"] is False
    assert report["generation"]["code"] == "OutOfMemoryError"
    assert backend.released
    assert json.loads((tmp_path / "benchmark.json").read_text())["generation"]["ok"] is False


def test_settings_the_backend_refuses_are_recorded(clip_with_audio: Path, tmp_path: Path) -> None:
    backend = StubBackend(verdict=ValidationResult(False, "UNSUPPORTED_CAPABILITY", "no"))
    assert benchmark(clip_with_audio, tmp_path, backend=backend)["generation"]["code"] == "UNSUPPORTED_CAPABILITY"


def test_no_photo_means_no_attempt_rather_than_a_failure(clip_with_audio: Path, tmp_path: Path) -> None:
    report = run(clip_with_audio, None, ["0.5,0.5"], tmp_path, tracker=FakeTracker(), pose=FakePose())
    assert report["generation"]["attempted"] is False


def test_an_empty_directory_has_no_contact_sheet(tmp_path: Path) -> None:
    assert contact_sheet(tmp_path, tmp_path / "sheet.png") is None
