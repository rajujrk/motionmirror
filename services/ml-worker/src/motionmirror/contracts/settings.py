"""Generation settings rules. The Java validator applies the same ones; shared fixtures keep them in step."""

from __future__ import annotations

from typing import Any

from motionmirror.contracts import load_validator

SCHEMA = "settings/generation-settings.v1.schema.json"


def first_error(settings: dict[str, Any]) -> str | None:
    """Return the code of the first rule these settings break, or None."""
    if any(load_validator(SCHEMA).iter_errors(settings)):
        return "SCHEMA"
    if settings["height"] % 16 or settings["width"] % 16:
        return "SIZE_NOT_DIVISIBLE_BY_16"
    segment = settings["segment_frame_length"]
    conditioning = settings["prev_segment_conditioning_frames"]
    if segment % 4 != 1:
        return "SEGMENT_NOT_4N_PLUS_1"
    if conditioning % 4 != 1:
        return "CONDITIONING_NOT_4N_PLUS_1"
    if segment < 2 * conditioning:
        return "SEGMENT_TOO_SHORT_FOR_CONDITIONING"
    if str(settings.get("negative_prompt") or "").strip() and settings["guidance_scale"] <= 1:
        return "NEGATIVE_PROMPT_REQUIRES_GUIDANCE"
    return None
