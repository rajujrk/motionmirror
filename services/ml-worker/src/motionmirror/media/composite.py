"""Blend generated pixels over the original. The pipeline regenerates everything, so this keeps the real scene."""

from __future__ import annotations

import cv2
import numpy as np


def soft_alpha(mask: np.ndarray, feather: int) -> np.ndarray:
    """An alpha map, fully opaque inside the mask, fading outward so hair and limbs aren't clipped at its edge."""
    binary = (mask > 127).astype(np.float32)
    if feather <= 0:
        return binary
    size = feather * 2 + 1
    grown = cv2.dilate(binary, np.ones((size, size), np.uint8))
    blurred = cv2.GaussianBlur(grown, (0, 0), sigmaX=max(feather / 2, 0.5))
    return np.maximum(binary, blurred)


def composite(source: np.ndarray, generated: np.ndarray, mask: np.ndarray, feather: int) -> np.ndarray:
    alpha = soft_alpha(mask, feather)[:, :, None]
    blended = source.astype(np.float32) * (1 - alpha) + generated.astype(np.float32) * alpha
    return np.clip(blended + 0.5, 0, 255).astype(np.uint8)
