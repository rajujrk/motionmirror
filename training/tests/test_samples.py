"""Sample building on a synthetic archive whose frames carry their own index, so alignment can be checked exactly."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from motionmirror.media.frames import read_frames
from PIL import Image

from motionmirror_training.datasets import samples
from motionmirror_training.datasets.registry import DatasetBlockedError, DatasetEntry, Review
from motionmirror_training.datasets.samples import (
    CONTROL_FRAMES,
    CONTROL_OFFSET,
    REFERENCE_GAP,
    TARGET_FRAMES,
    VIDEO_FRAMES,
    DatasetMaskTracker,
    Rejection,
    SampleRow,
    SplitLeakError,
    assert_no_leak,
    assess_masks,
    build_samples,
    choose_split,
    find_near_duplicates,
    fixed_eval_set,
    perceptual_hash,
)
from motionmirror_training.datasets.tiktok import UnsafeArchive, index_clips

# Above the minimum side, so a clip is only rejected for resolution when a test means it to be.
SIZE = (256, 448)
ROW_FIELDS = SampleRow.__dataclass_fields__


def entry(
    status: str = "approved", scope: str = "research_only", access: str = "kaggle"
) -> DatasetEntry:
    return DatasetEntry(
        id="tiny-dance",
        name="Tiny Dance",
        version="1",
        use="tests",
        primary_url="https://example.test",
        fallback_url=None,
        access=access,
        source_ref="x/y",
        license_name="CC BY-NC 4.0",
        license_url="https://creativecommons.org/licenses/by-nc/4.0/",
        license_notes="Research only.",
        attribution="Someone",
        consent_notes="Research adapters only.",
        size_bytes=1,
        checksum_manifest=None,
        retrieval_date="2026-11-06",
        review=Review(
            status=status,
            scope=scope,
            reviewer="Owner",
            review_date="2026-11-06",
            decision="Decided.",
        ),
    )


def index_color(index: int) -> int:
    """Brightness steps large enough to survive H.264, so a frame's index can be read back."""
    return 20 + (index % 8) * 30


def frame_png(index: int, pattern: int = 0, size: tuple[int, int] = SIZE) -> bytes:
    """Brightness encodes the frame. The pattern makes clips from different sources look different."""
    picture = np.full((size[1], size[0], 3), index_color(index), dtype=np.uint8)
    rng = np.random.default_rng(pattern)
    band = size[1] // 2 - 8
    picture[:band, :] = rng.integers(0, 255, (band, size[0], 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(picture).save(buffer, "PNG")
    return buffer.getvalue()


def mask_png(area: float = 0.25, size: tuple[int, int] = SIZE) -> bytes:
    mask = np.zeros((size[1], size[0]), dtype=np.uint8)
    side = int(size[1] * area**0.5), int(size[0] * area**0.5)
    mask[60 : 60 + side[0], 30 : 30 + side[1]] = 255
    buffer = io.BytesIO()
    Image.fromarray(mask).save(buffer, "PNG")
    return buffer.getvalue()


def write_clip(
    archive: zipfile.ZipFile,
    clip: str,
    frames: int,
    masks: int | None = None,
    area: float = 0.25,
    size: tuple[int, int] = SIZE,
) -> None:
    pattern = int(clip)
    for index in range(1, frames + 1):
        archive.writestr(
            f"TikTok_dataset/TikTok_dataset/{clip}/images/{index:04d}.png",
            frame_png(index - 1, pattern, size),
        )
    for index in range(1, (frames if masks is None else masks) + 1):
        archive.writestr(
            f"TikTok_dataset/TikTok_dataset/{clip}/masks/{index:04d}.png", mask_png(area, size)
        )


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("dataset") / "dataset.zip"
    with zipfile.ZipFile(path, "w") as output:
        write_clip(output, "00001", 100)
        write_clip(output, "00002", 40)  # too short
        write_clip(output, "00003", 100, masks=99)  # one mask missing
        write_clip(output, "00004", 100, area=0.001)  # dancer far too small
    return path


class FakePose:
    revision = "fake-pose"

    def estimate(self, frame: np.ndarray, box: tuple[int, int, int, int]) -> dict[str, Any]:
        return {"box": box}

    def face_crop(self, frame: np.ndarray, meta: dict[str, Any]) -> np.ndarray:
        return np.full((512, 512, 3), 90, dtype=np.uint8)

    def draw(self, meta: dict[str, Any], height: int, width: int) -> np.ndarray:
        return np.full((height, width, 3), 200, dtype=np.uint8)


@pytest.fixture(scope="module")
def built(archive: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Any]:
    out = tmp_path_factory.mktemp("built")
    return out, build_samples(archive, entry(), out, FakePose())


def brightness(frame: np.ndarray) -> int:
    """Read the index back from the area below the noise band, nearest step wins."""
    return int(np.median(frame[int(frame.shape[0] * 0.5) :]))


def expected(index: int) -> int:
    return index_color(index)


def test_an_unreviewed_dataset_is_refused_before_any_work(archive: Path, tmp_path: Path) -> None:
    with pytest.raises(DatasetBlockedError):
        build_samples(
            archive, entry(status="blocked", scope="none", access="blocked"), tmp_path, FakePose()
        )
    assert not (tmp_path / "manifest.json").exists()


def test_usable_clips_become_samples_and_the_rest_record_why_not(built: tuple[Path, Any]) -> None:
    _, report = built
    assert [sample.clip_id for sample in report.samples] == ["00001"]
    reasons = {rejection.clip_id: rejection.reason for rejection in report.rejections}
    assert reasons == {
        "00002": "too_short",
        "00003": "mask_count_mismatch",
        "00004": "subject_missing_or_too_small",
    }


def test_the_video_is_the_reference_then_the_targets(built: tuple[Path, Any]) -> None:
    out, report = built
    sample = report.samples[0]
    frames = read_frames(out / sample.video, 108, 192)
    assert len(frames) == VIDEO_FRAMES
    assert sample.reference_frame == 0
    assert abs(brightness(frames[0]) - expected(sample.reference_frame)) < 12
    start = sample.target_start_frame
    assert start == REFERENCE_GAP
    for position in (1, 2, 9, 40, 80):
        target_index = start + position - 1
        assert abs(brightness(frames[position]) - expected(target_index)) < 12, (
            f"video frame {position}"
        )


def test_the_reference_is_not_a_frame_the_model_must_produce(built: tuple[Path, Any]) -> None:
    _, report = built
    sample = report.samples[0]
    assert (
        not sample.target_start_frame
        <= sample.reference_frame
        < sample.target_start_frame + TARGET_FRAMES
    )


def test_controls_line_up_with_the_trainers_pairing(built: tuple[Path, Any]) -> None:
    """The trainer pairs video frame j with control j-4. Video j shows target j-1, so control k shows target k+3."""
    out, report = built
    sample = report.samples[0]
    background = read_frames(out / sample.animate_inpaint_video, 108, 192)
    video = read_frames(out / sample.video, 108, 192)
    assert len(background) == CONTROL_FRAMES
    assert len(background) == len(video) - 4
    for control in (0, 1, 8, 40, CONTROL_FRAMES - 1):
        source_index = sample.target_start_frame + CONTROL_OFFSET + control
        # The bottom edge is outside the dancer's edit region, so the original pixels survive there.
        seen = int(np.median(background[control][-8:]))
        assert abs(seen - expected(source_index)) < 12, f"control {control}"
        assert abs(int(np.median(video[control + 4][-8:])) - seen) < 12, (
            f"pairing at control {control}"
        )


def test_the_trainer_can_read_the_metadata(built: tuple[Path, Any]) -> None:
    out, report = built
    split = report.samples[0].split
    with (out / f"metadata_{split}.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    for column in (
        "video",
        "animate_pose_video",
        "animate_face_video",
        "animate_inpaint_video",
        "animate_mask_video",
    ):
        assert (out / rows[0][column]).exists(), column
    assert rows[0]["prompt"]


def test_the_manifest_has_what_the_brief_lists(built: tuple[Path, Any]) -> None:
    out, _ = built
    manifest = json.loads((out / "manifest.json").read_text())
    row = manifest["samples"][0]
    for name in (
        "source_sha256",
        "reference_frame",
        "dataset_id",
        "dataset_version",
        "license",
        "consent",
        "person_id",
        "split",
    ):
        assert row[name] not in (None, ""), name
    assert row["license_scope"] == "research_only"
    assert manifest["commercial_use_ok"] is False
    assert manifest["rejection_counts"]["too_short"] == 1


def test_the_dataset_card_states_the_terms_and_limits(built: tuple[Path, Any]) -> None:
    out, _ = built
    card = (out / "DATASET_CARD.md").read_text()
    assert "Research only." in card
    assert "not allowed" in card
    assert "no person ids" in card


def test_a_whole_group_lands_in_one_split_every_time() -> None:
    assert len({choose_split("seq_00001_00010") for _ in range(5)}) == 1
    names = {choose_split(f"seq_{index}") for index in range(300)}
    assert names == {"train", "val", "test"}


def sample_row(clip_id: str, split: str, group: str) -> SampleRow:
    values = {name: "x" for name in ROW_FIELDS}
    values.update(
        clip_id=clip_id, split=split, source_group=group, reference_frame=0, target_start_frame=8
    )
    return SampleRow(**values)


def test_the_same_source_in_two_splits_fails_the_build() -> None:
    with pytest.raises(SplitLeakError, match="seq_a"):
        assert_no_leak([sample_row("1", "train", "seq_a"), sample_row("2", "test", "seq_a")])


def test_different_sources_in_different_splits_pass() -> None:
    assert_no_leak([sample_row("1", "train", "seq_a"), sample_row("2", "test", "seq_b")])


def test_near_duplicates_are_found_despite_re_encoding() -> None:
    rng = np.random.default_rng(1)
    base = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    noisy = np.clip(base.astype(int) + rng.integers(-3, 3, base.shape), 0, 255).astype(np.uint8)
    other = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    hashes = {"a": perceptual_hash(base), "b": perceptual_hash(noisy), "c": perceptual_hash(other)}
    assert find_near_duplicates(hashes) == [("a", "b")]


def test_a_near_duplicate_across_sources_is_dropped_so_it_cant_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "dupes.zip"
    with zipfile.ZipFile(path, "w") as output:
        write_clip(output, "00001", 100)
        for index in range(1, 101):  # the same pictures again, as a different clip
            output.writestr(
                f"TikTok_dataset/TikTok_dataset/00050/images/{index:04d}.png",
                frame_png(index - 1, 1),
            )
            output.writestr(
                f"TikTok_dataset/TikTok_dataset/00050/masks/{index:04d}.png", mask_png()
            )
    report = build_samples(path, entry(), tmp_path / "out", FakePose())
    assert [sample.clip_id for sample in report.samples] == ["00001"]
    assert Rejection("00050", "near_duplicate_of_another_source") in report.rejections


def test_the_fixed_eval_set_comes_only_from_test_and_doesnt_change() -> None:
    rows = [
        sample_row(str(index), "test" if index % 2 else "train", f"g{index}") for index in range(20)
    ]
    chosen = fixed_eval_set(rows, 4)
    assert chosen == fixed_eval_set(list(reversed(rows)), 4)
    assert all(int(clip_id) % 2 for clip_id in chosen)


def test_masks_for_the_wrong_number_of_frames_are_refused() -> None:
    tracker = DatasetMaskTracker([np.zeros((4, 4), dtype=np.uint8)])
    with pytest.raises(ValueError, match="1 masks"):
        tracker.track([np.zeros((4, 4, 3), dtype=np.uint8)] * 2, [])


def test_a_low_resolution_clip_is_rejected_before_it_is_upscaled(tmp_path: Path) -> None:
    path = tmp_path / "dataset.zip"
    with zipfile.ZipFile(path, "w") as output:
        write_clip(output, "00009", 100, size=(108, 192))
    report = build_samples(path, entry(), tmp_path / "out", FakePose())
    assert report.rejections == [Rejection("00009", "too_low_resolution")]


def test_the_first_frame_gets_the_same_checks_as_every_other(tmp_path: Path) -> None:
    """It's decoded for the duplicate check before anything else, so it can't skip the archive's safety checks."""
    path = tmp_path / "dataset.zip"
    jpeg = io.BytesIO()
    Image.new("RGB", SIZE).save(jpeg, "JPEG")
    with zipfile.ZipFile(path, "w") as output:
        write_clip(output, "00001", 100)
        output.writestr("TikTok_dataset/TikTok_dataset/00002/images/0001.png", jpeg.getvalue())
    with zipfile.ZipFile(path) as archive:
        clip = index_clips(archive)["00002"]
        with pytest.raises(UnsafeArchive, match="isn't a PNG"):
            samples.first_frame(archive, clip)


def test_a_clip_whose_controls_come_out_a_frame_short_is_rejected_not_fatal(
    archive: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cutting by time can round to one frame more or less. That clip is dropped, and the build still finishes."""

    def one_frame_short(
        source: Path, inputs: Any, settings: Any, context: Any, tracker: Any, pose: Any
    ) -> None:
        tracker.track([None] * (len(tracker.masks) - 1), [])

    monkeypatch.setattr(samples, "run_preprocess", one_frame_short)
    out = tmp_path / "out"
    report = build_samples(archive, entry(), out, FakePose())
    assert Rejection("00001", "control_frame_mismatch") in report.rejections
    assert (out / "manifest.json").exists()


def test_a_clip_where_the_subject_fills_the_frame_is_rejected() -> None:
    assert assess_masks([np.ones((10, 10), dtype=np.uint8)] * 3) == "subject_fills_the_frame"


def test_a_short_gap_constant_keeps_the_target_range_the_right_length() -> None:
    assert (VIDEO_FRAMES - 1) % 4 == 0
    assert samples.MIN_SOURCE_FRAMES > TARGET_FRAMES
