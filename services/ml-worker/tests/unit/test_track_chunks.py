"""Chunked tracking: bounded memory must not change which frames get a mask or lose the dancer between chunks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("cv2")

from motionmirror.backends.base import JobContext
from motionmirror.media.ffmpeg import MediaError
from motionmirror.media.frames import FrameSource, VideoWriter, write_video
from motionmirror.preprocessing.mapping import FramePrompt
from motionmirror.preprocessing.pipeline import track_in_chunks

SIZE = 16


class IndexedSource:
    """Frames whose first pixel holds their index, so a chunk can be checked exactly."""

    def __init__(self, total: int) -> None:
        self.total = total

    def slice(self, begin: int, end: int) -> list[np.ndarray]:
        frames = []
        for index in range(begin, min(end, self.total)):
            frame = np.zeros((SIZE, SIZE, 3), dtype=np.uint16)
            frame[0, 0, 0] = index
            frames.append(frame)
        return frames


class RecordingTracker:
    revision = "recording"

    def __init__(self, empty_from: int | None = None, empty_to: int | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.empty = range(empty_from, empty_to) if empty_from is not None and empty_to is not None else range(0)

    def track(self, frames: list[np.ndarray], prompts: list[FramePrompt]) -> list[np.ndarray]:
        indices = [int(frame[0, 0, 0]) for frame in frames]
        self.calls.append({"indices": indices, "prompts": prompts})
        masks = []
        for index in indices:
            mask = np.zeros((SIZE, SIZE), dtype=np.uint8)
            if index not in self.empty:
                mask[index % SIZE, 0] = 1
            masks.append(mask)
        return masks


def mark(frame: int = 0) -> FramePrompt:
    return FramePrompt(frame, [(1.0, 1.0)], [1])


def is_mask_of(mask: np.ndarray, index: int) -> bool:
    return bool(mask[index % SIZE, 0] == 1)


def test_a_clip_shorter_than_a_chunk_is_one_session_with_only_the_users_mark() -> None:
    tracker = RecordingTracker()
    masks = track_in_chunks(tracker, IndexedSource(10), 10, [mark()], chunk_frames=20)
    assert len(tracker.calls) == 1
    assert len(masks) == 10
    assert all(prompt.mask is None for prompt in tracker.calls[0]["prompts"])


@pytest.mark.parametrize(("total", "chunk"), [(25, 10), (20, 10), (19, 10), (21, 10), (100, 7), (11, 10)])
def test_every_frame_gets_its_own_mask_exactly_once(total: int, chunk: int) -> None:
    tracker = RecordingTracker()
    masks = track_in_chunks(tracker, IndexedSource(total), total, [mark()], chunk_frames=chunk)
    assert len(masks) == total
    assert all(is_mask_of(mask, index) for index, mask in enumerate(masks))


def test_no_session_holds_more_than_a_chunk() -> None:
    tracker = RecordingTracker()
    track_in_chunks(tracker, IndexedSource(100), 100, [mark()], chunk_frames=12)
    assert max(len(call["indices"]) for call in tracker.calls) <= 12


def test_each_chunk_starts_from_the_last_chunks_final_mask() -> None:
    tracker = RecordingTracker()
    track_in_chunks(tracker, IndexedSource(25), 25, [mark()], chunk_frames=10)
    second = tracker.calls[1]
    assert second["indices"][0] == 9, "chunks share a frame"
    seed = second["prompts"][0]
    assert seed.frame == 0
    assert seed.mask is not None
    assert is_mask_of(seed.mask, 9)


def test_a_later_mark_lands_on_the_right_frame_of_its_chunk() -> None:
    tracker = RecordingTracker()
    track_in_chunks(tracker, IndexedSource(30), 30, [mark(0), mark(15)], chunk_frames=10)
    second = tracker.calls[1]
    assert second["indices"][0] == 9
    points = [prompt for prompt in second["prompts"] if prompt.points]
    assert [prompt.frame for prompt in points] == [6]


def test_a_mark_on_the_shared_frame_replaces_the_seed_because_sam2_cant_take_both() -> None:
    tracker = RecordingTracker()
    track_in_chunks(tracker, IndexedSource(25), 25, [mark(0), mark(9)], chunk_frames=10)
    prompts = tracker.calls[1]["prompts"]
    assert [prompt.frame for prompt in prompts] == [0]
    assert prompts[0].mask is None
    assert prompts[0].points


def test_frames_before_the_first_mark_are_tracked_backward_from_it() -> None:
    tracker = RecordingTracker()
    masks = track_in_chunks(tracker, IndexedSource(30), 30, [mark(15)], chunk_frames=10)
    assert len(masks) == 30
    assert all(is_mask_of(mask, index) for index, mask in enumerate(masks))
    assert tracker.calls[0]["indices"][0] == 15
    backward = tracker.calls[-1]
    assert backward["indices"][0] == 0
    assert backward["prompts"][-1].frame == len(backward["indices"]) - 1


def test_a_dancer_hidden_across_a_boundary_is_not_lost_for_good() -> None:
    tracker = RecordingTracker(empty_from=7, empty_to=12)
    masks = track_in_chunks(tracker, IndexedSource(25), 25, [mark()], chunk_frames=10)
    seed = tracker.calls[1]["prompts"][0]
    assert seed.mask is not None
    assert is_mask_of(seed.mask, 6), "the closest mask that still showed the dancer"
    assert not masks[9].any(), "the hidden frame stays hidden rather than borrowing the seed"
    assert is_mask_of(masks[20], 20)


def test_progress_reports_real_counts_that_only_go_up() -> None:
    seen: list[int] = []
    track_in_chunks(RecordingTracker(), IndexedSource(50), 50, [mark()], chunk_frames=10, on_progress=seen.append)
    assert seen == sorted(seen)
    assert seen[-1] == 50


def test_a_source_decodes_again_and_slices_without_keeping_the_clip(clip_with_audio: Path) -> None:
    source = FrameSource(clip_with_audio, 32, 32, fps=10)
    total = source.count()
    assert total == source.count()
    assert len(source.slice(2, 5)) == 3
    middle = source.slice(3, 4)[0]
    assert np.array_equal(middle, list(source)[3])


def test_a_writer_produces_a_playable_file_and_counts_frames(tmp_path: Path) -> None:
    writer = VideoWriter(tmp_path / "out.mp4", 10)
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    writer.write(frame)
    writer.write(frame)
    assert writer.close() == 2
    assert FrameSource(tmp_path / "out.mp4", 32, 32).count() == 2


def test_a_writer_with_nothing_written_raises(tmp_path: Path) -> None:
    with pytest.raises(MediaError, match="no frames"):
        VideoWriter(tmp_path / "out.mp4", 10).close()


def test_write_video_still_encodes_a_whole_clip(tmp_path: Path) -> None:
    frames = [np.zeros((32, 32, 3), dtype=np.uint8)] * 3
    assert write_video(frames, tmp_path / "out.mp4", 10) == 3


def test_a_cancelled_run_leaves_no_encoder_behind(clip_with_audio: Path, tmp_path: Path) -> None:
    from motionmirror.preprocessing.pipeline import PROGRESS_EVERY, run_preprocess
    from tests.unit.test_preprocess_logic import INPUTS, FakePose, FakeTracker

    class Stop(RuntimeError):
        pass

    class Cancelling(JobContext):
        """Lets tracking finish and stops during writing, which is when encoders are running."""

        calls = 0

        def check_cancelled(self) -> None:
            self.calls += 1
            if self.calls == 2:
                raise Stop

    settings = {"height": 128, "width": 96, "processing_fps": 30}
    assert FrameSource(clip_with_audio, 96, 128, fps=30).count() > PROGRESS_EVERY
    context = Cancelling(tmp_path)
    with pytest.raises(Stop):
        run_preprocess(clip_with_audio, INPUTS, settings, context, FakeTracker(), FakePose())
    assert context.calls == 2
    children = subprocess.run(["pgrep", "-P", str(os.getpid()), "ffmpeg"], capture_output=True, text=True, check=False)
    assert children.stdout == "", "an encoder outlived the attempt"


def test_a_dancer_never_visible_gives_empty_masks_for_the_failure_check_to_catch() -> None:
    tracker = RecordingTracker(empty_from=0, empty_to=1000)
    masks = track_in_chunks(tracker, IndexedSource(25), 25, [mark()], chunk_frames=10)
    assert len(masks) == 25
    assert not any(mask.any() for mask in masks)
    assert len(tracker.calls) == 1, "nothing to seed from, so no more model time is spent"
