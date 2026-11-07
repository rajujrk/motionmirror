"""Contract tests for the real Diffusers Wan-Animate pipeline, on tiny random weights.

These prove how the pinned pipeline code behaves. They say nothing about output quality, because
the weights are random. No real weights are downloaded.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("torch")
pytest.importorskip("diffusers")

import torch
from PIL import Image

from motionmirror.testing.tiny_wan import build_tiny_pipeline, replacement_inputs

pytestmark = pytest.mark.requires_wan

DEVICES = [
    "cpu",
    pytest.param(
        "mps",
        marks=[
            pytest.mark.requires_mps,
            pytest.mark.skipif(not torch.backends.mps.is_available(), reason="no MPS device"),
        ],
    ),
]


@pytest.fixture(scope="module", params=DEVICES)
def pipe(request: pytest.FixtureRequest) -> Any:
    return build_tiny_pipeline(device=request.param)


def run(pipe: Any, num_frames: int = 9, size: int = 16, **overrides: Any) -> torch.Tensor:
    inputs = replacement_inputs(num_frames=num_frames, size=size) | overrides
    return pipe(**inputs).frames[0]


def test_size_not_divisible_by_16_is_rejected(pipe: Any) -> None:
    with pytest.raises(ValueError, match="divisible by 16"):
        run(pipe, height=24, width=16)


def test_segment_length_not_4n_plus_1_is_floored(pipe: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    original = pipe.prepare_latents

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["num_frames"])
        return original(*args, **kwargs)

    monkeypatch.setattr(pipe, "prepare_latents", spy)
    run(pipe, segment_frame_length=11)
    assert seen[0] == 9


@pytest.mark.parametrize(
    ("num_frames", "segment", "prev"),
    [
        (9, 9, 1),
        (17, 9, 1),
        (25, 9, 1),
        (23, 9, 1),
        (5, 9, 1),
        (21, 13, 5),
        (33, 17, 5),
        (13, 13, 5),
    ],
)
def test_output_has_exactly_one_frame_per_pose_frame(pipe: Any, num_frames: int, segment: int, prev: int) -> None:
    video = run(
        pipe,
        num_frames=num_frames,
        segment_frame_length=segment,
        prev_segment_conditioning_frames=prev,
    )
    assert video.shape[0] == num_frames


@pytest.mark.parametrize(("num_frames", "segment", "prev"), [(21, 9, 5), (9, 9, 5)])
def test_segment_shorter_than_twice_the_conditioning_frames_fails(
    pipe: Any, num_frames: int, segment: int, prev: int
) -> None:
    """Pins an upstream limit: the validator must reject segment < 2 * prev before any GPU work."""
    with pytest.raises((RuntimeError, AssertionError)):
        run(
            pipe,
            num_frames=num_frames,
            segment_frame_length=segment,
            prev_segment_conditioning_frames=prev,
        )


@pytest.mark.parametrize(("num_frames", "prev"), [(1, 1), (5, 5), (3, 5)])
def test_clip_no_longer_than_the_conditioning_frames_fails(pipe: Any, num_frames: int, prev: int) -> None:
    """Pins an upstream limit: the validator must require num_frames > prev."""
    with pytest.raises(ValueError, match="non-empty"):
        run(
            pipe,
            num_frames=num_frames,
            segment_frame_length=13,
            prev_segment_conditioning_frames=prev,
        )


def test_precomputed_image_features_cant_be_passed_in(pipe: Any) -> None:
    """Pins an upstream limit: the reference image is also needed for its VAE latents, yet passing it alongside
    `image_embeds` is refused, and leaving it out fails. So `image_embeds` can't be used at all."""
    inputs = replacement_inputs()
    features = pipe.encode_image(inputs["image"], pipe.device)
    with pytest.raises(ValueError, match="Cannot forward both"):
        pipe(**inputs, image_embeds=features)
    without_image = {key: value for key, value in inputs.items() if key != "image"}
    with pytest.raises(Exception):  # noqa: B017 - any failure: upstream reads the missing image unguarded
        pipe(**without_image, image_embeds=features)


def test_image_features_can_be_reused_through_encode_image(pipe: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """What `spike_mac` relies on to free the image encoder before the transformer loads. Exact on the CPU; on MPS
    the separately computed features differ by about one float32 rounding step (measured 1.2e-7)."""
    expected = run(pipe)
    features = pipe.encode_image(replacement_inputs()["image"], pipe.device)
    monkeypatch.setattr(pipe, "encode_image", lambda image, device=None: features)
    torch.testing.assert_close(run(pipe), expected, rtol=0, atol=1e-6)


def test_white_mask_means_generate_and_black_means_preserve(pipe: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Inside the pipeline a known region is 1, so a white input must become 0 (unknown)."""
    captured: list[torch.Tensor] = []
    original = pipe.prepare_prev_segment_cond_latents

    def spy(*args: Any, **kwargs: Any) -> torch.Tensor:
        result = original(*args, **kwargs)
        captured.append(result[:, :4].detach().cpu())
        return result

    monkeypatch.setattr(pipe, "prepare_prev_segment_cond_latents", spy)

    size = 32
    half_white = Image.new("L", (size, size), 0)
    half_white.paste(255, (0, 0, size, size // 2))
    run(pipe, size=size, mask_video=[half_white] * 9)

    mask = captured[0]
    first_later_frame = mask[0, :, 1]
    height = first_later_frame.shape[-2]
    assert first_later_frame[:, : height // 2].max().item() < 0.01
    assert first_later_frame[:, height // 2 :].min().item() > 0.99


def test_pipeline_does_not_composite_the_background(pipe: Any) -> None:
    """Black-mask pixels are regenerated, so MotionMirror must composite against the source itself."""
    size = 32
    background = Image.new("RGB", (size, size), (200, 30, 30))
    video = run(
        pipe,
        size=size,
        background_video=[background] * 9,
        mask_video=[Image.new("L", (size, size), 0)] * 9,
    )
    expected = torch.tensor([200, 30, 30], dtype=video.dtype).view(3, 1, 1) / 255
    assert (video[0].cpu() - expected).abs().max().item() > 0.05


def test_negative_prompt_has_no_effect_at_default_guidance(pipe: Any) -> None:
    first = run(pipe, negative_prompt="blurry", guidance_scale=1.0)
    second = run(pipe, negative_prompt="sharp and bright", guidance_scale=1.0)
    assert torch.equal(first, second)


def test_negative_prompt_changes_output_when_guidance_is_enabled(pipe: Any) -> None:
    first = run(pipe, negative_prompt="blurry", guidance_scale=5.0)
    second = run(pipe, negative_prompt="sharp and bright", guidance_scale=5.0)
    assert not torch.equal(first, second)
