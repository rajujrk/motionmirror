"""The real backend code on tiny random weights: frame accounting, audio, compositing, offload. Not quality."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("torch")
pytest.importorskip("diffusers")

import torch

from motionmirror.backends.base import AdapterInput, GenerationRequest, JobContext
from motionmirror.backends.pipeline_offload import install_decoded_frame_offload
from motionmirror.backends.wan_animate import CONTROL_NAMES, WanAnimateReplacementBackend, WanConfig
from motionmirror.device import UnsupportedDeviceError
from motionmirror.media.frames import read_frames, write_video
from motionmirror.media.probe import probe
from motionmirror.testing.tiny_wan import build_tiny_pipeline, replacement_inputs
from tests.unit.conftest import run_ffmpeg

pytestmark = pytest.mark.requires_wan

SIZE = 32
FPS = 10
SETTINGS = {
    "schema_version": 1,
    "seed": 7,
    "height": SIZE,
    "width": SIZE,
    "num_inference_steps": 2,
    "guidance_scale": 1.0,
    "segment_frame_length": 9,
    "prev_segment_conditioning_frames": 1,
    "processing_fps": FPS,
    "mask_feather_pixels": 0,
    "retain_audio": True,
}


def make_controls(directory: Path, frames: int) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    half = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    half[: SIZE // 2] = 255
    streams = {
        "src_pose.mp4": [np.full((SIZE, SIZE, 3), 40, dtype=np.uint8)] * frames,
        "src_face.mp4": [np.full((512, 512, 3), 90, dtype=np.uint8)] * frames,
        "src_bg.mp4": [np.full((SIZE, SIZE, 3), 120, dtype=np.uint8)] * frames,
        "src_mask.mp4": [half] * frames,
    }
    for name, stream in streams.items():
        write_video(stream, directory / name, FPS)
    return {name: directory / name for name in CONTROL_NAMES}


@pytest.fixture(scope="module")
def source_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("wan") / "source.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", f"color=c=green:size={SIZE}x{SIZE}:rate={FPS}:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from PIL import Image

    path = tmp_path_factory.mktemp("ref") / "reference.png"
    Image.new("RGB", (SIZE, SIZE), (200, 50, 50)).save(path)
    return path


def backend() -> WanAnimateReplacementBackend:
    return WanAnimateReplacementBackend(
        WanConfig(device="cpu", pipeline_kwargs={"max_sequence_length": 16}),
        pipeline_factory=lambda _config: build_tiny_pipeline(),
    )


def request(
    source: Path, reference: Path, controls: dict[str, Path], settings: dict[str, Any] | None = None, **inputs: Any
) -> GenerationRequest:
    return GenerationRequest(
        job_id="job", kind="PREVIEW", settings={**SETTINGS, **(settings or {})},
        inputs={"primary_reference_asset_id": "ref-1", "supplementary_reference_asset_ids": ["other"],
                "source_sha256": "a" * 64, "primary_reference_sha256": "b" * 64, **inputs},
        source_path=source, primary_reference_path=reference, control_paths=controls,
    )  # fmt: skip


class Recording(JobContext):
    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.events: list[tuple[str, int | None, int | None]] = []

    def progress(
        self, stage: str, current: int | None = None, total: int | None = None, unit: str | None = None
    ) -> None:
        self.events.append((stage, current, total))


def run(
    source: Path, ref: Path, tmp_path: Path, frames: int = 20, settings: dict[str, Any] | None = None, **inputs: Any
) -> tuple[Any, Recording]:
    controls = make_controls(tmp_path / "controls", frames)
    context = Recording(tmp_path / "work")
    instance = backend()
    req = request(source, ref, controls, settings, **inputs)
    assert instance.validate(req).ok
    return instance.generate(instance.prepare(req), context), context


def test_output_length_and_audio_match_the_selection(source_clip: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run(source_clip, reference, tmp_path)
    output = probe(result.video_path)
    assert output.duration_seconds == pytest.approx(2.0, abs=1 / FPS + 0.05)
    assert output.audio_streams == 1
    assert output.video_codec == "h264"
    assert output.pixel_format == "yuv420p"


def test_a_preview_stops_at_its_seconds(source_clip: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run(source_clip, reference, tmp_path, preview_seconds=1)
    assert probe(result.video_path).duration_seconds == pytest.approx(1.0, abs=1 / FPS + 0.05)


def test_black_mask_pixels_keep_the_source_and_white_ones_are_generated(
    source_clip: Path, reference: Path, tmp_path: Path
) -> None:
    result, _ = run(source_clip, reference, tmp_path)
    source_frame = read_frames(source_clip, SIZE, SIZE, fps=FPS)[5]
    output_frame = read_frames(result.video_path, SIZE, SIZE)[5]
    bottom = np.abs(source_frame[SIZE * 3 // 4 :].astype(int) - output_frame[SIZE * 3 // 4 :].astype(int)).mean()
    top = np.abs(source_frame[: SIZE // 4].astype(int) - output_frame[: SIZE // 4].astype(int)).mean()
    assert bottom < 12, "the preserved region should match the original"
    assert top > bottom, "the edit region should differ"


def test_progress_reports_real_segments_in_order(source_clip: Path, reference: Path, tmp_path: Path) -> None:
    _, context = run(source_clip, reference, tmp_path)
    stages = [event[0] for event in context.events]
    assert (
        stages.index("prepare_reference")
        < stages.index("generate")
        < stages.index("composite")
        < stages.index("encode")
        < stages.index("verify")
    )
    generated = [event for event in context.events if event[0] == "generate" and event[1]]
    assert generated
    assert generated[-1][1] == generated[-1][2]


def test_the_manifest_records_what_was_used_and_no_prompts(source_clip: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run(source_clip, reference, tmp_path, settings={"positive_prompt": "a secret prompt"})
    text = result.manifest_path.read_text()
    assert "a secret prompt" not in text
    manifest = json.loads(text)
    assert manifest["references"]["consumed"] == ["ref-1"]
    assert manifest["references"]["stored_not_consumed"] == ["other"]
    assert manifest["model"]["revision"]
    assert manifest["device_type"] == "cpu"
    assert manifest["output_label"] == "experimental"
    assert manifest["may_satisfy_acceptance"] is False
    assert len(manifest["output_checksum"]) == 64


def test_settings_the_pipeline_would_crash_on_are_refused(source_clip: Path, reference: Path, tmp_path: Path) -> None:
    controls = make_controls(tmp_path / "controls", 20)
    instance = backend()
    bad = request(source_clip, reference, controls, {"segment_frame_length": 9, "prev_segment_conditioning_frames": 5})
    verdict = instance.validate(bad)
    assert not verdict.ok
    assert verdict.code == "UNSUPPORTED_CAPABILITY"


def test_a_negative_prompt_without_guidance_is_refused(source_clip: Path, reference: Path, tmp_path: Path) -> None:
    controls = make_controls(tmp_path / "controls", 20)
    verdict = backend().validate(request(source_clip, reference, controls, {"negative_prompt": "blurry"}))
    assert not verdict.ok


def test_missing_controls_are_refused(source_clip: Path, reference: Path) -> None:
    assert backend().validate(request(source_clip, reference, {})).code == "INVALID_REQUEST"


def test_a_cuda_capability_can_satisfy_acceptance_and_mps_cannot() -> None:
    real = WanAnimateReplacementBackend(WanConfig(device="cuda"))
    assert real.capabilities().output_label == "real"
    assert real.capabilities().may_satisfy_acceptance
    experimental = WanAnimateReplacementBackend(WanConfig(device="mps"))
    assert experimental.capabilities().output_label == "experimental"
    assert not experimental.capabilities().may_satisfy_acceptance


def test_the_cpu_profile_refuses_real_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MM_DEVICE_PROFILE", "cpu")
    with pytest.raises(UnsupportedDeviceError, match="mock mode"):
        WanAnimateReplacementBackend.from_environment()


def test_an_out_of_memory_error_keeps_its_own_failure_code() -> None:
    from motionmirror.worker.child import classify

    assert classify(torch.cuda.OutOfMemoryError("CUDA out of memory"))[0] == "CUDA_OOM"


@pytest.mark.parametrize(
    "device",
    ["cpu", pytest.param("mps", marks=pytest.mark.skipif(not torch.backends.mps.is_available(), reason="no MPS"))],
)
def test_frame_offload_gives_the_same_length_and_returns_cpu_frames(device: str) -> None:
    pipe = build_tiny_pipeline(device=device)
    install_decoded_frame_offload(pipe)
    inputs = replacement_inputs(num_frames=25) | {"segment_frame_length": 13, "prev_segment_conditioning_frames": 5}
    video = pipe(**inputs).frames[0]
    assert video.shape[0] == 25
    assert video.device.type == "cpu"


def test_frame_offload_does_not_change_the_result() -> None:
    inputs = replacement_inputs(num_frames=17)
    baseline = build_tiny_pipeline()(**inputs).frames[0]
    pipe = build_tiny_pipeline()
    install_decoded_frame_offload(pipe)
    assert torch.equal(baseline, pipe(**replacement_inputs(num_frames=17)).frames[0])


def adapter_file(directory: Path, scope: str = "research_only") -> AdapterInput:
    from safetensors.torch import save_file

    from tests.contract.test_adapter_key_compat import diffsynth_lora_state_dict

    path = directory / "adapter.safetensors"
    save_file(diffsynth_lora_state_dict(), str(path))
    return AdapterInput("adapter-1", "Dance", path, "c" * 64, 1.0, scope)


def test_a_named_adapter_really_attaches_and_the_manifest_says_so(
    source_clip: Path, reference: Path, tmp_path: Path
) -> None:
    controls = make_controls(tmp_path / "controls", 20)
    instance = backend()
    req = replace(request(source_clip, reference, controls), adapter=adapter_file(tmp_path))
    result = instance.generate(instance.prepare(req), Recording(tmp_path / "work"))
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["adapter"]["layers_attached"] > 0
    assert manifest["adapter"]["license_scope"] == "research_only"
    assert result.effective_settings["adapter_license_scope"] == "research_only"
    assert instance._pipe.get_active_adapters() == ["trained"]


def test_without_an_adapter_the_manifest_records_none(source_clip: Path, reference: Path, tmp_path: Path) -> None:
    result, _ = run(source_clip, reference, tmp_path)
    assert json.loads(result.manifest_path.read_text())["adapter"] is None


def test_an_adapter_for_another_model_fails_with_its_own_code(
    source_clip: Path, reference: Path, tmp_path: Path
) -> None:
    from safetensors.torch import save_file

    from motionmirror.backends.adapters import AdapterLoadError
    from motionmirror.worker.child import classify

    path = tmp_path / "other.safetensors"
    save_file({"blocks.0.self_attn.q.lora_A.default.weight": torch.zeros(4, 7)}, str(path))
    controls = make_controls(tmp_path / "controls", 20)
    instance = backend()
    req = replace(
        request(source_clip, reference, controls), adapter=AdapterInput("x", "x", path, "c" * 64, 1.0, "production")
    )
    with pytest.raises(AdapterLoadError) as failure:
        instance.generate(instance.prepare(req), Recording(tmp_path / "work"))
    assert classify(failure.value)[0] == "ADAPTER_INCOMPATIBLE"
    assert classify(failure.value)[2] is False


def test_the_mock_backend_refuses_an_adapter_rather_than_ignoring_it(
    source_clip: Path, reference: Path, tmp_path: Path
) -> None:
    from motionmirror.backends.mock import MockReplacementBackend

    req = replace(request(source_clip, reference, {}), adapter=adapter_file(tmp_path))
    assert MockReplacementBackend().validate(req).code == "UNSUPPORTED_CAPABILITY"


def test_the_download_is_marked_as_ai_edited(source_clip: Path, reference: Path, tmp_path: Path) -> None:
    import subprocess

    result, _ = run(source_clip, reference, tmp_path)
    tags = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "csv=p=0", str(result.video_path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert "AI-edited" in tags.stdout
