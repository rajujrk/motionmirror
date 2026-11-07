"""Load trained LoRA adapters and prove they attached.

Diffusers only logs a warning when no keys match, then runs without the adapter. A manifest would
then claim an adapter that had no effect, so a load that doesn't attach every layer is an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from torch import Tensor


class AdapterLoadError(RuntimeError):
    """The adapter file does not fully match the loaded model."""


@dataclass(frozen=True)
class LoadedAdapter:
    adapter_name: str
    layer_count: int
    scale: float


def normalize_lora_keys(state_dict: dict[str, Tensor]) -> dict[str, Tensor]:
    """Rewrite DiffSynth keys into the `diffusion_model.*` form that Diffusers converts."""
    normalized: dict[str, Tensor] = {}
    for key, value in state_dict.items():
        name = key.replace(".default.weight", ".weight")
        if not name.startswith("diffusion_model."):
            name = f"diffusion_model.{name}"
        normalized[name] = value
    return normalized


def _count_layers(state_dict: dict[str, Tensor]) -> int:
    return sum(1 for key in state_dict if ".lora_A." in key)


def _count_attached(transformer: Any, adapter_name: str) -> int:
    return sum(
        1
        for _, module in transformer.named_modules()
        if adapter_name in getattr(module, "lora_A", {})
    )


def load_adapter(
    pipe: Any, state_dict: dict[str, Tensor], adapter_name: str, scale: float
) -> LoadedAdapter:
    expected = _count_layers(state_dict)
    if expected == 0:
        raise AdapterLoadError("adapter file contains no LoRA layers")

    try:
        pipe.load_lora_weights(normalize_lora_keys(state_dict), adapter_name=adapter_name)
    except (KeyError, ValueError, IndexError) as error:
        raise AdapterLoadError(f"adapter does not match the model: {error}") from error

    attached = _count_attached(pipe.transformer, adapter_name)
    if attached != expected:
        if attached:
            pipe.delete_adapters(adapter_name)
        raise AdapterLoadError(f"attached {attached} of {expected} adapter layers")

    pipe.set_adapters([adapter_name], [scale])
    return LoadedAdapter(adapter_name=adapter_name, layer_count=attached, scale=scale)
