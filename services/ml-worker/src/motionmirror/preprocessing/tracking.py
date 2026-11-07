"""Judging a track. A tracker can drift onto someone else without saying so, so this flags it for review."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

MIN_AREA_FRACTION = 0.0005
MAX_LOST_FRACTION = 0.5
MAX_JUMP_FRACTION = 0.2


class TargetLost(RuntimeError):
    """The dancer couldn't be followed through enough of the clip."""


@dataclass(frozen=True)
class TrackReport:
    total_frames: int
    lost_frames: list[int]
    jump_frames: list[int]

    def warnings(self) -> list[str]:
        notes = []
        if self.lost_frames:
            notes.append(
                f"The dancer isn't visible in {len(self.lost_frames)} frames, starting at frame {self.lost_frames[0]}."
            )
        if self.jump_frames:
            notes.append(f"The highlight jumps at frame {self.jump_frames[0]}. Check it stays on the same person.")
        return notes

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "warnings": self.warnings()}


def assess_track(masks: list[np.ndarray]) -> TrackReport:
    if not masks:
        raise TargetLost("The clip has no frames to track")
    height, width = masks[0].shape[:2]
    diagonal = float(np.hypot(width, height))
    minimum_area = height * width * MIN_AREA_FRACTION
    lost: list[int] = []
    jumps: list[int] = []
    previous: np.ndarray | None = None
    for index, mask in enumerate(masks):
        if mask.sum() < minimum_area:
            lost.append(index)
            continue
        ys, xs = np.nonzero(mask)
        centre = np.array([xs.mean(), ys.mean()])
        if previous is not None and np.linalg.norm(centre - previous) > diagonal * MAX_JUMP_FRACTION:
            jumps.append(index)
        previous = centre
    report = TrackReport(total_frames=len(masks), lost_frames=lost, jump_frames=jumps)
    if len(lost) > len(masks) * MAX_LOST_FRACTION:
        raise TargetLost(
            f"The dancer was lost at frame {lost[0]} and is missing from {len(lost)} of {len(masks)} frames. "
            "Add marks on the frames where tracking goes wrong."
        )
    return report
