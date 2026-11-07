from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cv2")

from motionmirror.media.composite import composite, soft_alpha


def test_outside_the_mask_keeps_the_original_exactly() -> None:
    source = np.full((64, 64, 3), 200, dtype=np.uint8)
    generated = np.full((64, 64, 3), 10, dtype=np.uint8)
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[20:40, 20:40] = 255
    result = composite(source, generated, mask, feather=2)
    assert result[0, 0].tolist() == [200, 200, 200]
    assert result[30, 30].tolist() == [10, 10, 10]


def test_feathering_only_softens_outward_so_limbs_are_not_clipped() -> None:
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[20:40, 20:40] = 255
    alpha = soft_alpha(mask, feather=4)
    assert (alpha[mask > 127] == 1).all()
    assert 0 < alpha[30, 18] < 1
    assert alpha[0, 0] == pytest.approx(0, abs=0.01)


def test_no_feather_gives_a_hard_edge() -> None:
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[2:4, 2:4] = 255
    assert set(np.unique(soft_alpha(mask, 0))) == {0.0, 1.0}
