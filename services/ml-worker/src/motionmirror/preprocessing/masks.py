"""Mask operations matching the official Wan preprocessing, so the model sees what it was trained on."""

from __future__ import annotations

import cv2
import numpy as np

# The upstream example uses --iterations 3 --k 7 --w_len 1 --h_len 1. Three passes of a 7x7 square grow a mask by
# 3 pixels each, which is exactly one pass of a 19x19 square: 9 pixels.
DEFAULT_EXPAND_PIXELS = 9
BLOCK_COLUMNS = 1
BLOCK_ROWS = 1


def bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x1, y1, x2, y2) in pixels, or None if the mask is empty."""
    rows = np.flatnonzero(mask.any(axis=1))
    columns = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or columns.size == 0:
        return None
    return int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1


def expand_mask(
    mask: np.ndarray, expand_pixels: int = DEFAULT_EXPAND_PIXELS, columns: int = BLOCK_COLUMNS, rows: int = BLOCK_ROWS
) -> np.ndarray:
    """The edit region: dilated, then filled in blocks across the person's box. One block is the whole box.

    A roomier region gives the new person space for different hair, clothes and proportions. It costs some background
    consistency, which the final composite limits.
    """
    size = expand_pixels * 2 + 1
    grown = cv2.dilate(mask.astype(np.uint8), np.ones((size, size), np.uint8))
    box = bbox_from_mask(grown)
    if box is None:
        return grown
    x1, y1, x2, y2 = box
    width, height = max((x2 - x1) // columns, 1), max((y2 - y1) // rows, 1)
    for left in range(x1, x2, width):
        for top in range(y1, y2, height):
            region = grown[top : min(top + height, y2), left : min(left + width, x2)]
            if region.any():
                region[:] = 1
    return grown


def background_frame(frame: np.ndarray, edit_mask: np.ndarray) -> np.ndarray:
    """The scene with the edit region blacked out, as src_bg.mp4 expects."""
    return frame * (1 - edit_mask[:, :, None])


def mask_frame(edit_mask: np.ndarray) -> np.ndarray:
    """White means generate and black means preserve. Verified against the pinned pipeline."""
    return np.repeat(edit_mask[:, :, None] * 255, 3, axis=2).astype(np.uint8)
