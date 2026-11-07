from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from motionmirror.media.ffmpeg import MediaError, run_ffmpeg
from motionmirror.media.probe import probe


def test_probe_reads_rational_rate_duration_and_audio(clip_with_audio: Path) -> None:
    info = probe(clip_with_audio)
    assert info.frame_rate == Fraction(30, 1)
    assert info.duration_seconds == pytest.approx(2.0, abs=0.1)
    assert info.width == 320
    assert info.height == 240
    assert info.audio_streams == 1
    assert info.video_codec == "h264"


def test_probe_reports_a_silent_clip(silent_clip: Path) -> None:
    assert probe(silent_clip).audio_streams == 0


def test_rotation_metadata_swaps_display_dimensions(rotated_clip: Path) -> None:
    info = probe(rotated_clip)
    assert info.rotation in (90, 270, -90)
    assert (info.width, info.height) == (320, 240)
    assert (info.display_width, info.display_height) == (240, 320)


def test_variable_frame_rate_is_detected(vfr_clip: Path) -> None:
    info = probe(vfr_clip)
    assert info.is_vfr
    assert info.duration_seconds == pytest.approx(3.0, abs=0.2)


def test_constant_frame_rate_is_not_flagged_as_variable(clip_with_audio: Path) -> None:
    assert not probe(clip_with_audio).is_vfr


def test_several_audio_streams_are_counted(multi_audio_clip: Path) -> None:
    assert probe(multi_audio_clip).audio_streams == 2


def test_probing_something_that_is_not_media_fails(tmp_path: Path) -> None:
    fake = tmp_path / "fake.mp4"
    fake.write_text("<html><script>alert(1)</script></html>")
    with pytest.raises(MediaError):
        probe(fake)


def test_ffmpeg_refuses_network_inputs(tmp_path: Path) -> None:
    with pytest.raises(MediaError):
        run_ffmpeg(["-i", "http://169.254.169.254/latest/meta-data", str(tmp_path / "x.mp4")], timeout=20)


def test_a_hostile_filename_is_never_interpreted_by_a_shell(tmp_path: Path, clip_with_audio: Path) -> None:
    hostile = tmp_path / "$(touch pwned); rm -rf x.mp4"
    hostile.write_bytes(clip_with_audio.read_bytes())
    assert probe(hostile).width == 320
    assert not (tmp_path / "pwned").exists()


def test_a_clip_that_cannot_be_decoded_raises_rather_than_returning_no_frames(tmp_path: Path) -> None:
    from motionmirror.media.ffmpeg import MediaError
    from motionmirror.media.frames import read_frames

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video at all")
    with pytest.raises(MediaError, match="decoding failed"):
        read_frames(broken, 32, 32)


def test_stopping_early_does_not_count_as_a_decode_failure(clip_with_audio: Path) -> None:
    from motionmirror.media.frames import iter_frames

    stream = iter_frames(clip_with_audio, 32, 32)
    assert next(stream).shape == (32, 32, 3)
    stream.close()


def test_a_failed_encode_raises(tmp_path: Path) -> None:
    import numpy as np

    from motionmirror.media.ffmpeg import MediaError
    from motionmirror.media.frames import write_video

    odd = np.zeros((33, 33, 3), dtype=np.uint8)  # H.264 with yuv420p refuses odd sides
    with pytest.raises(MediaError, match="encoding failed"):
        write_video([odd], tmp_path / "out.mp4", 10)
