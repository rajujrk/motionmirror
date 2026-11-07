from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from motionmirror.backends.base import GenerationRequest, JobContext
from motionmirror.backends.mock import MOCK_LABEL, MockReplacementBackend
from motionmirror.media.probe import probe


def frame(path: Path, index_seconds: float = 0.0) -> np.ndarray:
    info = probe(path)
    data = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(index_seconds), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    return np.frombuffer(data, dtype=np.uint8).reshape(info.display_height, info.display_width, 3)


class RecordingContext(JobContext):
    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace=workspace)
        self.stages: list[str] = []

    def progress(
        self, stage: str, current: int | None = None, total: int | None = None, unit: str | None = None
    ) -> None:
        self.stages.append(stage)


def request(source: Path, reference: Path | None, **inputs: object) -> GenerationRequest:
    return GenerationRequest(
        job_id="job-1",
        kind="PREVIEW",
        settings={"seed": 7, "height": 832, "width": 480, "retain_audio": True, "positive_prompt": "secret prompt"},
        inputs={
            "source_sha256": "a" * 64,
            "primary_reference_sha256": "b" * 64,
            "supplementary_reference_asset_ids": ["c", "d"],
            **inputs,
        },
        source_path=source,
        primary_reference_path=reference,
    )


@pytest.fixture
def reference(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "reference.png"
    Image.new("RGB", (64, 128), (10, 200, 10)).save(path)
    return path


def run_backend(req: GenerationRequest, tmp_path: Path) -> tuple[object, RecordingContext]:
    backend = MockReplacementBackend()
    assert backend.validate(req).ok
    context = RecordingContext(tmp_path)
    return backend.generate(backend.prepare(req), context), context


def test_output_keeps_duration_and_audio(clip_with_audio: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run_backend(request(clip_with_audio, reference), tmp_path)
    source = probe(clip_with_audio)
    output = probe(result.video_path)
    assert output.duration_seconds == pytest.approx(source.duration_seconds, abs=1 / 30 + 0.05)
    assert output.audio_streams == 1
    assert output.video_codec == "h264"
    assert output.pixel_format == "yuv420p"


def test_a_silent_input_gives_a_valid_silent_output(silent_clip: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run_backend(request(silent_clip, reference), tmp_path)
    assert probe(result.video_path).audio_streams == 0


def test_several_audio_streams_keep_the_first(multi_audio_clip: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run_backend(request(multi_audio_clip, reference), tmp_path)
    assert probe(result.video_path).audio_streams == 1


def test_every_frame_is_visibly_labelled(clip_with_audio: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run_backend(request(clip_with_audio, reference), tmp_path)
    for seconds in (0.0, 1.0, 1.9):
        banner = frame(result.video_path, seconds)[:20, :, :].mean(axis=(0, 1))
        assert banner[0] > 150 and banner[1] < 90 and banner[2] < 90, "banner must be conspicuous red"


def test_the_manifest_says_mock_and_records_what_was_used(
    clip_with_audio: Path, reference: Path, tmp_path: Path
) -> None:
    result, _ = run_backend(request(clip_with_audio, reference), tmp_path)
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["label"] == MOCK_LABEL == "MOCK / NO AI REPLACEMENT"
    assert manifest["output_label"] == "mock"
    assert manifest["device_type"] == "cpu"
    assert manifest["references"]["consumed"] == []
    assert manifest["references"]["stored_not_consumed"] == ["c", "d"]
    assert manifest["input_checksums"]["source"] == "a" * 64
    assert len(manifest["output_checksum"]) == 64


def test_the_manifest_never_contains_prompts(clip_with_audio: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run_backend(request(clip_with_audio, reference), tmp_path)
    assert "secret prompt" not in result.manifest_path.read_text()


def test_a_preview_is_limited_to_its_seconds(clip_with_audio: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run_backend(request(clip_with_audio, reference, preview_seconds=1), tmp_path)
    assert probe(result.video_path).duration_seconds == pytest.approx(1.0, abs=0.1)


def test_a_trim_is_applied_with_aligned_audio(clip_with_audio: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run_backend(request(clip_with_audio, reference, trim_start_seconds=0.5, trim_end_seconds=1.5), tmp_path)
    output = probe(result.video_path)
    assert output.duration_seconds == pytest.approx(1.0, abs=0.1)
    assert output.audio_streams == 1


def test_a_rotated_source_keeps_its_display_orientation(rotated_clip: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run_backend(request(rotated_clip, reference), tmp_path)
    output = probe(result.video_path)
    assert (output.display_width, output.display_height) == (240, 320)


def test_variable_frame_rate_keeps_its_timing(vfr_clip: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run_backend(request(vfr_clip, reference), tmp_path)
    assert probe(result.video_path).duration_seconds == pytest.approx(probe(vfr_clip).duration_seconds, abs=0.15)


def test_output_is_deterministic(clip_with_audio: Path, reference: Path, tmp_path: Path) -> None:
    first, _ = run_backend(request(clip_with_audio, reference), tmp_path / "a")
    second, _ = run_backend(request(clip_with_audio, reference), tmp_path / "b")
    assert (frame(first.video_path, 1.0) == frame(second.video_path, 1.0)).all()


def test_stages_are_reported_in_order(clip_with_audio: Path, reference: Path, tmp_path: Path) -> None:
    _, context = run_backend(request(clip_with_audio, reference), tmp_path)
    assert context.stages == ["generate", "composite", "encode", "verify"]


def test_capabilities_say_mock_and_cpu() -> None:
    capabilities = MockReplacementBackend().capabilities()
    assert capabilities.backend_id == "mock"
    assert capabilities.device_type == "cpu"
    assert capabilities.output_label == "mock"
    assert capabilities.may_satisfy_acceptance is False


def test_importing_the_mock_backend_does_not_import_torch() -> None:
    code = (
        "import sys; import motionmirror.backends.mock, motionmirror.worker.child; "
        "print('torch' in sys.modules, 'diffusers' in sys.modules)"
    )
    output = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True).stdout
    assert output.strip() == "False False"
