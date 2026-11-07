from __future__ import annotations

import pytest

from motionmirror.device import UnsupportedDeviceError, select_device


def test_cpu_is_always_available() -> None:
    assert select_device("cpu").device == "cpu"


def test_unknown_profile_is_refused() -> None:
    with pytest.raises(UnsupportedDeviceError, match="quantum"):
        select_device("quantum")  # type: ignore[arg-type]


def test_cuda_without_a_gpu_fails_instead_of_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("motionmirror.device._cuda_available", lambda: False)
    with pytest.raises(UnsupportedDeviceError, match="CUDA"):
        select_device("cuda")


def test_mps_without_apple_gpu_fails_instead_of_falling_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("motionmirror.device._mps_available", lambda: False)
    with pytest.raises(UnsupportedDeviceError, match="MPS"):
        select_device("mps")


def test_available_cuda_is_selected_as_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("motionmirror.device._cuda_available", lambda: True)
    selection = select_device("cuda")
    assert selection.device == "cuda"
    assert selection.may_satisfy_acceptance


def test_mps_and_cpu_can_never_satisfy_acceptance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("motionmirror.device._mps_available", lambda: True)
    assert not select_device("mps").may_satisfy_acceptance
    assert not select_device("cpu").may_satisfy_acceptance
