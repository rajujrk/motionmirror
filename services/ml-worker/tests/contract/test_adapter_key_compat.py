"""A DiffSynth-trained LoRA must load in the Diffusers runtime that serves production."""

from __future__ import annotations

import pytest

pytest.importorskip("torch")
pytest.importorskip("diffusers")

import torch

from motionmirror.backends.adapters import AdapterLoadError, load_adapter
from motionmirror.testing.tiny_wan import build_tiny_pipeline, replacement_inputs

pytestmark = pytest.mark.requires_wan

RANK = 4
INNER_DIM = 24
FFN_DIM = 32
NUM_BLOCKS = 2


def diffsynth_lora_state_dict(seed: int = 7) -> dict[str, torch.Tensor]:
    """Keys as DiffSynth exports them with `--remove_prefix_in_ckpt pipe.dit.`, targets q,k,v,o,ffn.0,ffn.2."""
    generator = torch.Generator().manual_seed(seed)
    state: dict[str, torch.Tensor] = {}

    def add(prefix: str, in_dim: int, out_dim: int) -> None:
        state[f"{prefix}.lora_A.default.weight"] = torch.randn(RANK, in_dim, generator=generator)
        state[f"{prefix}.lora_B.default.weight"] = torch.randn(out_dim, RANK, generator=generator)

    for block in range(NUM_BLOCKS):
        for attention in ("self_attn", "cross_attn"):
            for layer in ("q", "k", "v", "o"):
                add(f"blocks.{block}.{attention}.{layer}", INNER_DIM, INNER_DIM)
        add(f"blocks.{block}.ffn.0", INNER_DIM, FFN_DIM)
        add(f"blocks.{block}.ffn.2", FFN_DIM, INNER_DIM)
    return state


def test_diffsynth_keys_load_and_change_output() -> None:
    pipe = build_tiny_pipeline()
    baseline = pipe(**replacement_inputs()).frames[0]

    load_adapter(pipe, diffsynth_lora_state_dict(), adapter_name="trained", scale=1.0)
    adapted = pipe(**replacement_inputs()).frames[0]

    assert not torch.equal(baseline, adapted)


def test_adapter_scale_zero_restores_baseline() -> None:
    pipe = build_tiny_pipeline()
    baseline = pipe(**replacement_inputs()).frames[0]

    load_adapter(pipe, diffsynth_lora_state_dict(), adapter_name="trained", scale=0.0)
    disabled = pipe(**replacement_inputs()).frames[0]

    assert torch.allclose(baseline, disabled, atol=1e-5)


def test_raw_diffsynth_keys_are_silently_ignored_by_diffusers() -> None:
    """Why `load_adapter` exists: upstream only logs a warning, then runs without the adapter."""
    pipe = build_tiny_pipeline()
    pipe.load_lora_weights(diffsynth_lora_state_dict(), adapter_name="trained")
    assert "trained" not in pipe.get_list_adapters().get("transformer", [])


def test_load_adapter_counts_every_layer_as_attached() -> None:
    pipe = build_tiny_pipeline()
    state = diffsynth_lora_state_dict()

    loaded = load_adapter(pipe, state, adapter_name="trained", scale=1.0)

    assert loaded.layer_count == len(state) // 2
    assert loaded.adapter_name in pipe.get_list_adapters()["transformer"]


def test_load_adapter_rejects_a_file_that_matches_nothing() -> None:
    pipe = build_tiny_pipeline()
    junk = {
        "something.else.lora_A.weight": torch.zeros(RANK, INNER_DIM),
        "something.else.lora_B.weight": torch.zeros(INNER_DIM, RANK),
    }
    with pytest.raises(AdapterLoadError):
        load_adapter(pipe, junk, adapter_name="junk", scale=1.0)


def test_load_adapter_rejects_a_partial_match() -> None:
    pipe = build_tiny_pipeline()
    state = diffsynth_lora_state_dict()
    state["blocks.99.self_attn.q.lora_A.default.weight"] = torch.zeros(RANK, INNER_DIM)
    state["blocks.99.self_attn.q.lora_B.default.weight"] = torch.zeros(INNER_DIM, RANK)
    with pytest.raises(AdapterLoadError):
        load_adapter(pipe, state, adapter_name="partial", scale=1.0)
