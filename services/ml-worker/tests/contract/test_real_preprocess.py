"""Real SAM 2 and ViTPose on a real clip. Needs make models-download PROFILE=preprocess and MM_TEST_CLIP_DIR."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")

from motionmirror.backends.base import JobContext
from motionmirror.media.frames import read_frames
from motionmirror.preprocessing.models import model_cache
from motionmirror.preprocessing.pipeline import run_preprocess

pytestmark = [pytest.mark.requires_weights, pytest.mark.timeout(1800)]

CLIP_DIR = os.environ.get("MM_TEST_CLIP_DIR")
SIZE = (432, 768)
FRAMES = 24
SETTINGS = {"width": SIZE[0], "height": SIZE[1], "processing_fps": 30, "mask_expand_pixels": 9}


@pytest.fixture(scope="module")
def clip() -> Path:
    if not CLIP_DIR:
        pytest.skip("set MM_TEST_CLIP_DIR to a folder holding video.mp4 and dataset_mask.mp4")
    if not (model_cache() / "sam2.1-hiera-small").exists():
        pytest.skip("run make models-download PROFILE=preprocess")
    return Path(CLIP_DIR)


@pytest.fixture(scope="module")
def outputs(clip: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, np.ndarray]:
    import cv2

    truth = read_frames(clip / "dataset_mask.mp4", *SIZE)[0][:, :, 0] > 127
    distance = cv2.distanceTransform(truth.astype(np.uint8), cv2.DIST_L2, 5)
    y, x = np.unravel_index(int(distance.argmax()), distance.shape)
    inputs = {
        "annotations": {
            "points": [{"x": float(x) / SIZE[0], "y": float(y) / SIZE[1], "positive": True, "frame": 0}],
        },
        "frame_index": 0,
        "trim_start_seconds": 0.0,
        "trim_end_seconds": FRAMES / 30,
    }
    directory = tmp_path_factory.mktemp("real")
    run_preprocess(clip / "video.mp4", inputs, SETTINGS, JobContext(directory))
    return directory, truth


def test_every_control_has_the_same_length(outputs: tuple[Path, np.ndarray]) -> None:
    directory, _ = outputs
    for name in ("src_pose.mp4", "src_bg.mp4", "src_mask.mp4", "preview.mp4"):
        assert len(read_frames(directory / name, *SIZE)) == FRAMES, name
    assert len(read_frames(directory / "src_face.mp4", 512, 512)) == FRAMES


def test_the_edit_region_covers_the_dancer(outputs: tuple[Path, np.ndarray]) -> None:
    directory, truth = outputs
    edit = read_frames(directory / "src_mask.mp4", *SIZE)[0][:, :, 0] > 127
    assert (edit & truth).sum() / truth.sum() > 0.95


def test_the_background_has_the_dancer_removed(outputs: tuple[Path, np.ndarray]) -> None:
    directory, truth = outputs
    background = read_frames(directory / "src_bg.mp4", *SIZE)[0]
    assert background[truth].mean() < 20


def test_the_pose_is_drawn_on_a_black_canvas_around_the_dancer(outputs: tuple[Path, np.ndarray]) -> None:
    directory, truth = outputs
    pose = read_frames(directory / "src_pose.mp4", *SIZE)[0].max(axis=2) > 40
    assert pose.mean() < 0.2, "mostly black"
    ys, xs = np.nonzero(truth)
    box = np.zeros_like(pose)
    box[ys.min() : ys.max(), xs.min() : xs.max()] = True
    assert (pose & box).sum() / pose.sum() > 0.8, "drawn over the dancer"


def test_the_face_crop_is_real_image_content(outputs: tuple[Path, np.ndarray]) -> None:
    directory, _ = outputs
    face = read_frames(directory / "src_face.mp4", 512, 512)[0]
    assert face.std() > 10


def test_the_manifest_labels_this_by_device(outputs: tuple[Path, np.ndarray]) -> None:
    directory, _ = outputs
    manifest = json.loads((directory / "preprocess_manifest.json").read_text())
    expected = "real" if os.environ.get("MM_DEVICE_PROFILE") == "cuda" else "experimental"
    assert manifest["output_label"] == expected
    assert "sam2.1" in manifest["tool_revision"]
