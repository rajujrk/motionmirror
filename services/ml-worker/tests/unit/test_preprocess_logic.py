"""The whole preprocessing pipeline with a fake tracker and pose model, so it needs no weights."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("cv2")

from motionmirror.backends.base import JobContext
from motionmirror.media.frames import content_rect, iter_frames, read_frames
from motionmirror.media.probe import probe
from motionmirror.preprocessing.mapping import (
    FramePrompt,
    SelectionOutsideClip,
    build_prompts,
    processing_frame,
    to_model_point,
)
from motionmirror.preprocessing.masks import background_frame, bbox_from_mask, expand_mask, mask_frame
from motionmirror.preprocessing.pipeline import run_preprocess
from motionmirror.preprocessing.tracking import TargetLost, assess_track
from motionmirror.worker.failures import AttemptFailure

SETTINGS = {"height": 128, "width": 96, "processing_fps": 10}
INPUTS = {"annotations": {"points": [{"x": 0.5, "y": 0.5, "positive": True, "frame": 0}]}, "frame_index": 0}


def square_mask(height: int = 128, width: int = 96, x: int = 40, y: int = 50, size: int = 20) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[y : y + size, x : x + size] = 1
    return mask


class FakeTracker:
    revision = "fake-tracker"

    def __init__(self, make_masks: Any = None) -> None:
        self.prompts: list[FramePrompt] = []
        self.make_masks = make_masks

    def track(self, frames: list[np.ndarray], prompts: list[FramePrompt]) -> list[np.ndarray]:
        self.prompts = prompts
        if self.make_masks:
            return self.make_masks(len(frames))
        return [square_mask() for _ in frames]


class FakePose:
    revision = "fake-pose"

    def __init__(self) -> None:
        self.boxes: list[Any] = []

    def estimate(self, frame: np.ndarray, box: tuple[int, int, int, int]) -> dict[str, Any]:
        self.boxes.append(box)
        return {"box": box}

    def face_crop(self, frame: np.ndarray, meta: dict[str, Any]) -> np.ndarray:
        return np.full((512, 512, 3), 90, dtype=np.uint8)

    def draw(self, meta: dict[str, Any], height: int, width: int) -> np.ndarray:
        return np.full((height, width, 3), 200, dtype=np.uint8)


def test_model_point_matches_the_web_coordinates() -> None:
    # The same case as coords.test.ts: a portrait source on a differently shaped canvas.
    x, y = to_model_point(0.31, 0.77, (1080, 1920), (480, 832))
    scale = 832 / 1920
    assert x == pytest.approx(0.31 * 1080 * scale + (480 - 1080 * scale) / 2)
    assert y == pytest.approx(0.77 * 1920 * scale)


def test_a_source_frame_maps_by_time_not_by_index() -> None:
    assert processing_frame(30, Fraction(30), 10, 0.0, 100) == 10
    assert processing_frame(45, Fraction(30), 10, 1.0, 100) == 5
    assert processing_frame(5, Fraction(30), 10, 1.0, 100) is None


def test_ntsc_rates_stay_exact() -> None:
    # Frame 300 at 29.97 fps is exactly 10.01 seconds, which is frame 300 at 30 fps after rounding.
    assert processing_frame(300, Fraction(30000, 1001), 30, 0.0, 1000) == 300
    assert processing_frame(3000, Fraction(30000, 1001), 30, 0.0, 4000) == 3003


def test_marks_group_by_frame_and_keep_polarity() -> None:
    annotations = {
        "points": [
            {"x": 0.5, "y": 0.5, "positive": True, "frame": 0},
            {"x": 0.1, "y": 0.1, "positive": False, "frame": 0},
            {"x": 0.6, "y": 0.6, "positive": True, "frame": 30},
        ],
        "box": {"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.6, "frame": 30},
    }
    prompts = build_prompts(annotations, (96, 128), (96, 128), Fraction(30), 10, 0.0, 20, 0)
    assert [prompt.frame for prompt in prompts] == [0, 10]
    assert prompts[0].labels == [1, 0]
    assert prompts[1].box == pytest.approx((19.2, 25.6, 57.6, 102.4))


def test_only_negative_marks_are_not_a_selection() -> None:
    annotations = {"points": [{"x": 0.5, "y": 0.5, "positive": False, "frame": 0}]}
    with pytest.raises(SelectionOutsideClip):
        build_prompts(annotations, (96, 128), (96, 128), Fraction(30), 10, 0.0, 20, 0)


def test_marks_outside_the_trim_are_refused() -> None:
    with pytest.raises(SelectionOutsideClip):
        build_prompts(INPUTS["annotations"], (96, 128), (96, 128), Fraction(30), 10, 5.0, 20, 0)


def test_mask_polarity_is_white_to_generate() -> None:
    frame = mask_frame(square_mask())
    assert frame[60, 50].tolist() == [255, 255, 255]
    assert frame[0, 0].tolist() == [0, 0, 0]


def test_background_blacks_out_only_the_edit_region() -> None:
    picture = np.full((128, 96, 3), 120, dtype=np.uint8)
    result = background_frame(picture, square_mask())
    assert result[60, 50].tolist() == [0, 0, 0]
    assert result[0, 0].tolist() == [120, 120, 120]


def test_the_edit_region_is_roomier_than_the_person() -> None:
    mask = square_mask()
    expanded = expand_mask(mask)
    assert expanded.sum() > mask.sum()
    assert (expanded[mask.astype(bool)] == 1).all()


def test_an_empty_mask_stays_empty() -> None:
    empty = np.zeros((128, 96), dtype=np.uint8)
    assert expand_mask(empty).sum() == 0
    assert bbox_from_mask(empty) is None


def test_a_brief_occlusion_is_a_warning_not_a_failure() -> None:
    masks = [square_mask() for _ in range(20)]
    masks[7] = np.zeros_like(masks[0])
    report = assess_track(masks)
    assert report.lost_frames == [7]
    assert "frame 7" in report.warnings()[0]


def test_a_mostly_lost_dancer_fails_and_names_the_frame() -> None:
    empty = np.zeros((128, 96), dtype=np.uint8)
    masks = [square_mask() for _ in range(5)] + [empty for _ in range(15)]
    with pytest.raises(TargetLost, match="frame 5"):
        assess_track(masks)


def test_a_sudden_jump_is_flagged_as_a_possible_switch_to_someone_else() -> None:
    masks = [square_mask(x=5, y=5) for _ in range(5)] + [square_mask(x=70, y=100) for _ in range(5)]
    report = assess_track(masks)
    assert report.jump_frames == [5]
    assert "jumps at frame 5" in " ".join(report.warnings())


def test_the_pipeline_writes_every_control_with_the_same_frame_count(clip_with_audio: Path, tmp_path: Path) -> None:
    tracker, pose = FakeTracker(), FakePose()
    result = run_preprocess(clip_with_audio, INPUTS, SETTINGS, JobContext(tmp_path), tracker, pose)
    names = {output["name"] for output in result["outputs"]}
    assert names == {
        "src_pose.mp4",
        "src_face.mp4",
        "src_bg.mp4",
        "src_mask.mp4",
        "preview.mp4",
        "preprocess_manifest.json",
    }
    frames = result["effective_settings"]["frames"]
    assert frames == 20
    for name in ("src_pose.mp4", "src_bg.mp4", "src_mask.mp4", "preview.mp4"):
        assert len(read_frames(tmp_path / name, 96, 128)) == frames
    assert len(read_frames(tmp_path / "src_face.mp4", 512, 512)) == frames


def test_the_mask_video_survives_encoding_with_the_right_polarity(clip_with_audio: Path, tmp_path: Path) -> None:
    run_preprocess(clip_with_audio, INPUTS, SETTINGS, JobContext(tmp_path), FakeTracker(), FakePose())
    frame = next(iter_frames(tmp_path / "src_mask.mp4", 96, 128))
    assert frame[60, 50, 0] > 200
    assert frame[2, 2, 0] < 50


def test_pose_gets_boxes_from_the_mask_and_none_for_hidden_frames(clip_with_audio: Path, tmp_path: Path) -> None:
    def masks(count: int) -> list[np.ndarray]:
        result = [square_mask() for _ in range(count)]
        result[3] = np.zeros_like(result[0])
        return result

    pose = FakePose()
    result = run_preprocess(clip_with_audio, INPUTS, SETTINGS, JobContext(tmp_path), FakeTracker(masks), pose)
    assert pose.boxes[0] == (40, 50, 60, 70)
    assert len(pose.boxes) == 19, "the pose model must not choose a person itself when the dancer is hidden"
    assert result["effective_settings"]["track"]["lost_frames"] == [3]


def test_a_lost_dancer_becomes_a_clear_failure(clip_with_audio: Path, tmp_path: Path) -> None:
    def masks(count: int) -> list[np.ndarray]:
        return [np.zeros((128, 96), dtype=np.uint8) for _ in range(count)]

    with pytest.raises(AttemptFailure) as failure:
        run_preprocess(clip_with_audio, INPUTS, SETTINGS, JobContext(tmp_path), FakeTracker(masks), FakePose())
    assert failure.value.code == "TARGET_LOST"


def test_a_trim_limits_the_frames_and_shifts_the_prompts(clip_with_audio: Path, tmp_path: Path) -> None:
    tracker = FakeTracker()
    inputs = {
        "annotations": {"points": [{"x": 0.5, "y": 0.5, "positive": True, "frame": 45}]},
        "trim_start_seconds": 1.0,
        "trim_end_seconds": 1.8,
    }
    result = run_preprocess(clip_with_audio, inputs, SETTINGS, JobContext(tmp_path), tracker, FakePose())
    assert result["effective_settings"]["frames"] == 8
    assert tracker.prompts[0].frame == 5


def test_a_rotated_clip_is_processed_upright(rotated_clip: Path, tmp_path: Path) -> None:
    info = probe(rotated_clip)
    assert (info.display_width, info.display_height) == (240, 320)
    frames = read_frames(rotated_clip, 96, 128, fps=10)
    assert frames
    assert content_rect(240, 320, 96, 128) == (0, 0, 96, 128)


def test_variable_frame_rate_resamples_by_time(vfr_clip: Path) -> None:
    assert len(read_frames(vfr_clip, 96, 128, fps=10)) == pytest.approx(30, abs=1)


def test_the_manifest_records_what_ran_and_no_marks(clip_with_audio: Path, tmp_path: Path) -> None:
    run_preprocess(clip_with_audio, INPUTS, SETTINGS, JobContext(tmp_path), FakeTracker(), FakePose())
    manifest = json.loads((tmp_path / "preprocess_manifest.json").read_text())
    assert manifest["tool_revision"] == "fake-tracker+fake-pose"
    assert manifest["mask_polarity"].startswith("white")


def test_one_large_dilation_equals_the_official_three_small_ones() -> None:
    import cv2

    mask = square_mask(height=128, width=128, x=50, y=50, size=20)
    official = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=3)
    assert np.array_equal(cv2.dilate(mask, np.ones((19, 19), np.uint8)), official)


def test_the_expand_setting_changes_the_edit_region(clip_with_audio: Path, tmp_path: Path) -> None:
    def edit_area(pixels: int, name: str) -> float:
        out = tmp_path / name
        settings = {**SETTINGS, "mask_expand_pixels": pixels}
        run_preprocess(clip_with_audio, INPUTS, settings, JobContext(out), FakeTracker(), FakePose())
        return float((read_frames(out / "src_mask.mp4", 96, 128)[0][:, :, 0] > 127).mean())

    assert edit_area(16, "wide") > edit_area(0, "tight")


def test_an_unreadable_clip_is_a_clear_failure_not_an_empty_one(tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video")
    with pytest.raises(AttemptFailure) as failure:
        run_preprocess(broken, INPUTS, SETTINGS, JobContext(tmp_path / "out"), FakeTracker(), FakePose())
    assert failure.value.code in {"INVALID_MEDIA"}
