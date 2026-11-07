from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from motionmirror.media.validation import (
    RejectedMedia,
    validate_image,
    validate_video,
)


def jpeg_with_orientation(path: Path, orientation: int, size: tuple[int, int] = (40, 20)) -> None:
    image = Image.new("RGB", size, (200, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = orientation
    exif[0x010F] = "SecretCameraMake"
    image.save(path, "JPEG", exif=exif)


def test_video_within_limits_passes_with_metadata(clip_with_audio: Path) -> None:
    media = validate_video(clip_with_audio, max_seconds=30)
    assert media["duration_seconds"] == pytest.approx(2.0, abs=0.1)
    assert media["frame_rate"] == "30/1"
    assert media["display_width"] == 320
    assert media["audio_streams"] == 1
    assert media["needs_trim"] is False


def test_a_clip_over_the_limit_needs_a_trim(clip_with_audio: Path) -> None:
    assert validate_video(clip_with_audio, max_seconds=1)["needs_trim"] is True


def test_a_far_too_long_clip_is_rejected(clip_with_audio: Path) -> None:
    with pytest.raises(RejectedMedia, match="too long"):
        validate_video(clip_with_audio, max_seconds=0.1)


def test_html_named_as_video_is_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "clip.mp4"
    fake.write_text("<html></html>")
    with pytest.raises(RejectedMedia):
        validate_video(fake, max_seconds=30)


def test_an_audio_only_file_is_rejected(tmp_path: Path) -> None:
    from tests.unit.conftest import run_ffmpeg

    path = tmp_path / "audio.mp4"
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(path))
    with pytest.raises(RejectedMedia, match="video"):
        validate_video(path, max_seconds=30)


def test_exif_orientation_is_applied_and_metadata_removed(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    jpeg_with_orientation(source, orientation=6)

    normalized = tmp_path / "normalized.png"
    media = validate_image(source, normalized)

    with Image.open(normalized) as image:
        assert image.size == (20, 40)
        assert not image.getexif()
    assert media["width"] == 20
    assert media["height"] == 40
    assert b"SecretCameraMake" not in normalized.read_bytes()


def test_svg_is_rejected_even_when_named_png(tmp_path: Path) -> None:
    fake = tmp_path / "photo.png"
    fake.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    with pytest.raises(RejectedMedia):
        validate_image(fake, tmp_path / "out.png")


def test_a_decompression_bomb_is_rejected_before_decoding(tmp_path: Path) -> None:
    bomb = tmp_path / "bomb.png"
    Image.new("L", (4000, 4000)).save(bomb, "PNG")
    with pytest.raises(RejectedMedia, match="pixels"):
        validate_image(bomb, tmp_path / "out.png", max_pixels=1_000_000)


def test_a_truncated_image_is_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (1, 2, 3)).save(buffer, "PNG")
    broken = tmp_path / "broken.png"
    broken.write_bytes(buffer.getvalue()[:60])
    with pytest.raises(RejectedMedia):
        validate_image(broken, tmp_path / "out.png")


def test_a_small_close_up_gets_a_warning(tmp_path: Path) -> None:
    small = tmp_path / "small.jpg"
    Image.new("RGB", (120, 120), (9, 9, 9)).save(small, "JPEG")
    media = validate_image(small, tmp_path / "out.png")
    assert "LOW_RESOLUTION" in media["warnings"]
