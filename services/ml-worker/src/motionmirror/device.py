"""Explicit device selection. An unsupported choice fails; a CUDA path never silently moves elsewhere."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DeviceProfile = Literal["cuda", "mps", "cpu"]


class UnsupportedDeviceError(RuntimeError):
    """The configured device profile can't run on this machine."""


@dataclass(frozen=True)
class DeviceSelection:
    device: DeviceProfile
    may_satisfy_acceptance: bool


def _cuda_available() -> bool:
    import torch

    return bool(torch.cuda.is_available())


def _mps_available() -> bool:
    import torch

    return bool(torch.backends.mps.is_available())


def select_device(profile: DeviceProfile) -> DeviceSelection:
    if profile == "cuda":
        if not _cuda_available():
            raise UnsupportedDeviceError(
                "Profile cuda was selected but no CUDA GPU is visible. Check `make doctor`. "
                "This never falls back to CPU or MPS. Use mock mode or a remote GPU worker."
            )
        return DeviceSelection("cuda", may_satisfy_acceptance=True)
    if profile == "mps":
        if not _mps_available():
            raise UnsupportedDeviceError("Profile mps was selected but MPS is not available on this machine.")
        return DeviceSelection("mps", may_satisfy_acceptance=False)
    if profile == "cpu":
        return DeviceSelection("cpu", may_satisfy_acceptance=False)
    raise UnsupportedDeviceError(f"Unknown device profile {profile!r}. Use cuda, mps or cpu.")
