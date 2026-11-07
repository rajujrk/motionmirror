"""A DiffSynth-trained LoRA must load in the Diffusers runtime that serves production."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("torch")
pytest.importorskip("diffusers")

import torch

from motionmirror.backends.adapters import AdapterLoadError, load_adapter, normalize_lora_keys
from motionmirror.testing.tiny_wan import build_tiny_pipeline, build_tiny_transformer, replacement_inputs

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


REAL_CONFIG = Path(__file__).parent / "fixtures" / "wan_animate_14b_transformer_config.json"


def _block_layers(transformer: Any) -> set[str]:
    return {
        name.removeprefix("blocks.0.").split(".0.")[0]
        for name, _ in transformer.named_modules()
        if name.startswith("blocks.0.attn2.")
    }


def _real_skeleton() -> Any:
    """The real architecture from its pinned config, with no weights. So no download and almost no memory."""
    from accelerate import init_empty_weights
    from diffusers import WanAnimateTransformer3DModel

    config = {key: value for key, value in json.loads(REAL_CONFIG.read_text()).items() if not key.startswith("_")}
    with init_empty_weights():
        return WanAnimateTransformer3DModel.from_config(config)


def test_the_tiny_model_has_the_same_cross_attention_layers_as_the_real_one() -> None:
    """A tiny model that drifts from the real one tests the wrong code, and nothing else would notice."""
    assert _block_layers(_real_skeleton()) == _block_layers(build_tiny_transformer())


def test_the_real_model_has_the_image_layers_that_get_zero_weight_lora() -> None:
    assert json.loads(REAL_CONFIG.read_text())["added_kv_proj_dim"] is not None
    assert "attn2.add_k_proj" in _block_layers(_real_skeleton())


def test_every_trainer_key_exists_on_the_real_model() -> None:
    """The mapping checked against all 40 real blocks, for the time when a real adapter finally exists."""
    pipe = build_tiny_pipeline()
    real = {name for name, _ in _real_skeleton().named_modules()}
    converted = pipe.lora_state_dict(normalize_lora_keys(_lora_for_blocks(40)))
    layers = {key.removeprefix("transformer.").rsplit(".lora_", 1)[0] for key in converted}
    assert len(layers) == 400
    assert not layers - real


def _lora_for_blocks(blocks: int) -> dict[str, torch.Tensor]:
    state: dict[str, torch.Tensor] = {}
    small = torch.zeros(1, 1)
    for block in range(blocks):
        for attention in ("self_attn", "cross_attn"):
            for layer in ("q", "k", "v", "o"):
                state[f"blocks.{block}.{attention}.{layer}.lora_A.default.weight"] = small
                state[f"blocks.{block}.{attention}.{layer}.lora_B.default.weight"] = small
        for layer in ("ffn.0", "ffn.2"):
            state[f"blocks.{block}.{layer}.lora_A.default.weight"] = small
            state[f"blocks.{block}.{layer}.lora_B.default.weight"] = small
    return state


def test_zero_weight_image_layers_arent_mistaken_for_a_mismatch() -> None:
    """This is the case that would have refused every real adapter."""
    pipe = build_tiny_pipeline()
    loaded = load_adapter(pipe, diffsynth_lora_state_dict(), adapter_name="trained", scale=1.0)
    assert loaded.layer_count == 20
    attached = [name for name, module in pipe.transformer.named_modules() if "trained" in getattr(module, "lora_A", {})]
    assert len(attached) > loaded.layer_count, "the expansion really did attach here, as it will on the real model"


def test_those_zero_weight_layers_change_nothing() -> None:
    pipe = build_tiny_pipeline()
    load_adapter(pipe, diffsynth_lora_state_dict(), adapter_name="trained", scale=1.0)
    for name, module in pipe.transformer.named_modules():
        if name.endswith((".attn2.add_k_proj", ".attn2.add_v_proj")):
            assert not module.lora_B["trained"].weight.any(), name


def test_a_file_missing_layers_the_model_has_still_loads_only_what_it_names() -> None:
    state = {key: value for key, value in diffsynth_lora_state_dict().items() if ".ffn." not in key}
    loaded = load_adapter(build_tiny_pipeline(), state, adapter_name="trained", scale=1.0)
    assert loaded.layer_count == 16
