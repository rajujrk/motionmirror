"""A real training loop on tiny random weights: data loading, optimizer steps, checkpoint, resume and export.

The conditioning comes from the serving pipeline's own forward call on real sample frames, and the exported file loads
through the serving adapter path. So the plumbing is real. The weights are not, and nothing here says anything about
quality. Runs are labelled smoke and can never satisfy the training gate.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import re
import resource
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from motionmirror.backends.adapters import load_adapter
from motionmirror.device import select_device
from motionmirror.media.frames import FrameSource
from motionmirror.testing.tiny_wan import TINY_FACE_SIZE, build_tiny_pipeline, replacement_inputs
from peft import LoraConfig
from peft.utils import get_peft_model_state_dict
from PIL import Image
from safetensors.torch import load_file, save_file

from motionmirror_training.train.profiles import Profile

LATENT_CHANNELS = 16
ADAPTER_NAME = "smoke"
# The diffusers layers that correspond to the trainer's q, k, v, o, ffn.0 and ffn.2.
TARGET_MODULES = (
    r"blocks\.\d+\.(attn1|attn2)\.(to_q|to_k|to_v|to_out\.0)|blocks\.\d+\.ffn\.net\.(0\.proj|2)"
)
DIFFSYNTH_NAMES = (
    (".attn1.", ".self_attn."),
    (".attn2.", ".cross_attn."),
    (".to_q.", ".q."),
    (".to_k.", ".k."),
    (".to_v.", ".v."),
    (".to_out.0.", ".o."),
    (".ffn.net.0.proj.", ".ffn.0."),
    (".ffn.net.2.", ".ffn.2."),
)


class _Captured(Exception):
    """Stops the pipeline at its first transformer call, once the arguments have been seen."""


class NoSamplesError(RuntimeError):
    """The sample directory has nothing to train on."""


@dataclass
class Conditioned:
    clip_id: str
    latents: torch.Tensor
    arguments: dict[str, Any]


def read_samples(samples_dir: Path) -> list[dict[str, Any]]:
    manifest = samples_dir / "manifest.json"
    if not manifest.exists():
        raise NoSamplesError(f"No manifest at {manifest}. Run make samples-build first")
    rows = json.loads(manifest.read_text())["samples"]
    if not rows:
        raise NoSamplesError(f"{manifest} lists no samples")
    return rows


def train_samples(samples_dir: Path) -> list[dict[str, Any]]:
    """Validation and test clips are what evaluation scores, so no training path ever reads them."""
    rows = [row for row in read_samples(samples_dir) if row["split"] == "train"]
    if not rows:
        raise NoSamplesError(f"{samples_dir} has no samples in the train split")
    return rows


def _frames(path: Path, width: int, height: int, count: int) -> list[Image.Image]:
    frames = FrameSource(path, width, height).slice(0, count)
    if len(frames) < count:
        raise NoSamplesError(f"{path} has {len(frames)} frames, this profile needs {count}")
    return [Image.fromarray(frame) for frame in frames]


def condition_sample(
    pipe: Any, samples_dir: Path, row: dict[str, Any], profile: Profile
) -> Conditioned:
    """Real frames through the real pipeline, up to the point where the transformer would be called."""
    height, width, count = profile.video.height, profile.video.width, profile.video.num_frames
    video = _frames(samples_dir / row["video"], width, height, count)
    captured: dict[str, Any] = {}

    def capture(_module: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        captured.update(kwargs)
        if args:
            captured["hidden_states"] = args[0]
        raise _Captured

    handle = pipe.transformer.register_forward_pre_hook(capture, with_kwargs=True)
    inputs = {
        **replacement_inputs(count, height),
        "image": video[0],
        "pose_video": _frames(samples_dir / row["animate_pose_video"], width, height, count),
        "face_video": _frames(
            samples_dir / row["animate_face_video"], TINY_FACE_SIZE, TINY_FACE_SIZE, count
        ),
        "background_video": _frames(
            samples_dir / row["animate_inpaint_video"], width, height, count
        ),
        "mask_video": [
            image.convert("L")
            for image in _frames(samples_dir / row["animate_mask_video"], width, height, count)
        ],
        "prompt": "a person dancing",
        "width": width,
        "height": height,
        "segment_frame_length": count,
    }
    try:
        pipe(**inputs)
        raise RuntimeError("the pipeline never called its transformer")
    except _Captured:
        pass
    finally:
        handle.remove()

    device = captured["hidden_states"].device
    pixels = (
        torch.from_numpy(np.stack([np.asarray(frame) for frame in video])).float().div(127.5).sub(1)
    )
    pixels = pixels.permute(3, 0, 1, 2)[None].to(device)
    with torch.no_grad():
        encoded = pipe.vae.encode(pixels).latent_dist.mode()
    shape = captured["hidden_states"][:, :LATENT_CHANNELS].shape
    latents = torch.nn.functional.interpolate(encoded, size=shape[2:], mode="trilinear")
    return Conditioned(
        row["clip_id"],
        latents.detach(),
        {key: value for key, value in captured.items() if key != "timestep"},
    )


def attach_lora(transformer: Any, profile: Profile) -> list[torch.nn.Parameter]:
    transformer.requires_grad_(False)
    transformer.add_adapter(
        LoraConfig(
            r=profile.lora.rank,
            lora_alpha=profile.lora.rank,
            target_modules=TARGET_MODULES,
            init_lora_weights=True,
        ),
        adapter_name=ADAPTER_NAME,
    )
    transformer.set_adapter(ADAPTER_NAME)
    trainable = [parameter for name, parameter in transformer.named_parameters() if "lora_" in name]
    for parameter in trainable:
        parameter.requires_grad_(True)
    transformer.train()
    return trainable


def train_step(
    pipe: Any, sample: Conditioned, step: int, seed: int, optimizer: torch.optim.Optimizer
) -> float:
    """Flow matching, seeded per step so a resumed run takes the same path as an unbroken one."""
    generator = torch.Generator("cpu").manual_seed(seed + step)
    device = sample.latents.device
    noise = torch.randn(sample.latents.shape, generator=generator).to(device)
    sigma = torch.rand(1, generator=generator).item()
    noisy = (1 - sigma) * sample.latents + sigma * noise
    arguments = dict(sample.arguments)
    condition = arguments.pop("hidden_states")[:, LATENT_CHANNELS:]
    predicted = pipe.transformer(
        hidden_states=torch.cat([noisy, condition], dim=1),
        timestep=torch.tensor([sigma * 1000.0], device=device),
        **{**arguments, "return_dict": False},
    )[0]
    loss = torch.nn.functional.mse_loss(predicted, noise - sample.latents)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    return float(loss.item())


def export_state(transformer: Any) -> dict[str, torch.Tensor]:
    """In the trainer's key format, so export is proven against the same loader a real adapter goes through."""
    state: dict[str, torch.Tensor] = {}
    for key, value in get_peft_model_state_dict(transformer, adapter_name=ADAPTER_NAME).items():
        name = key
        for ours, theirs in DIFFSYNTH_NAMES:
            name = name.replace(ours, theirs)
        name = re.sub(r"\.lora_(A|B)\.weight$", r".lora_\1.default.weight", name)
        state[name] = value.detach().cpu().contiguous()
    return state


def save_checkpoint(
    directory: Path,
    step: int,
    parameters: list[torch.nn.Parameter],
    optimizer: torch.optim.Optimizer,
) -> Path:
    """Safetensors and JSON rather than a pickle: a checkpoint is data, and loading one can't run code."""
    path = directory / f"step-{step:06d}"
    path.mkdir(parents=True, exist_ok=True)
    tensors: dict[str, torch.Tensor] = {}
    counters: dict[str, float] = {}
    for index, parameter in enumerate(parameters):
        tensors[f"weight.{index}"] = parameter.detach().cpu().contiguous()
        state = optimizer.state.get(parameter, {})
        for name, value in state.items():
            if isinstance(value, torch.Tensor) and value.dim() > 0:
                tensors[f"optimizer.{index}.{name}"] = value.detach().cpu().contiguous()
            else:
                counters[f"{index}.{name}"] = float(value)
    save_file(tensors, str(path / "state.safetensors"))
    (path / "progress.json").write_text(json.dumps({"step": step, "counters": counters}))
    return path


def load_checkpoint(
    path: Path, parameters: list[torch.nn.Parameter], optimizer: torch.optim.Optimizer
) -> int:
    tensors = load_file(str(path / "state.safetensors"))
    progress = json.loads((path / "progress.json").read_text())
    with torch.no_grad():
        for index, parameter in enumerate(parameters):
            parameter.copy_(tensors[f"weight.{index}"].to(parameter.device))
    for index, parameter in enumerate(parameters):
        state: dict[str, Any] = {}
        prefix = f"optimizer.{index}."
        for name, value in tensors.items():
            if name.startswith(prefix):
                state[name[len(prefix) :]] = value.to(parameter.device)
        for name, value in progress["counters"].items():
            owner, _, field = name.partition(".")
            if int(owner) == index:
                state[field] = torch.tensor(value, device="cpu")
        if state:
            optimizer.state[parameter] = state
    return int(progress["step"])


def latest_checkpoint(directory: Path) -> Path | None:
    done = sorted(path for path in directory.glob("step-*") if (path / "progress.json").exists())
    return done[-1] if done else None


def verify_export(path: Path, device: str, height: int) -> dict[str, Any]:
    """A fresh pipeline, the serving loader and a real change in output, or the export doesn't count."""
    pipe = build_tiny_pipeline(device)
    size = {"size": height}
    before = pipe(**replacement_inputs(**size)).frames[0]
    loaded = load_adapter(pipe, load_file(str(path)), "exported", 1.0)
    after = pipe(**replacement_inputs(**size)).frames[0]
    return {"layers_attached": loaded.layer_count, "output_changed": not torch.equal(before, after)}


def _peak_memory_gb() -> float:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(value / (1024**3 if sys.platform == "darwin" else 1024**2), 2)


def run_smoke(
    profile: Profile, samples_dir: Path, out: Path, seed: int = 0, stop_after: int | None = None
) -> dict[str, Any]:
    if profile.kind != "smoke":
        raise ValueError(f"{profile.id} isn't a smoke profile")
    device = select_device(profile.device).device
    rows = train_samples(samples_dir)
    out.mkdir(parents=True, exist_ok=True)
    checkpoints = out / "checkpoints"

    random.seed(seed)
    torch.manual_seed(seed)
    pipe = build_tiny_pipeline(device)
    conditioned = [condition_sample(pipe, samples_dir, row, profile) for row in rows]
    parameters = attach_lora(pipe.transformer, profile)
    optimizer = torch.optim.AdamW(parameters, lr=profile.learning_rate)

    resume = latest_checkpoint(checkpoints)
    step = load_checkpoint(resume, parameters, optimizer) if resume else 0
    resumed_from = step
    assert profile.steps is not None and profile.checkpoint_every is not None
    existing = out / "run_manifest.json"
    losses_so_far: list[float] = (
        json.loads(existing.read_text())["losses"] if resume and existing.exists() else []
    )
    last = profile.steps if stop_after is None else min(stop_after, profile.steps)
    losses = losses_so_far[:step]
    while step < last:
        losses.append(train_step(pipe, conditioned[step % len(conditioned)], step, seed, optimizer))
        step += 1
        if step % profile.checkpoint_every == 0 or step == last:
            save_checkpoint(checkpoints, step, parameters, optimizer)

    adapter = out / "adapter.safetensors"
    save_file(
        export_state(pipe.transformer),
        str(adapter),
        metadata={"label": "smoke"},
    )
    digest = hashlib.sha256(adapter.read_bytes()).hexdigest()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "label": "smoke",
        "profile": profile.id,
        "kind": profile.kind,
        "can_satisfy_training_gate": False,
        "device_type": device,
        "torch": torch.__version__,
        "base_model": "tiny-random, not a real model",
        "hyperparameters": {
            "learning_rate": profile.learning_rate,
            "steps": profile.steps,
            "optimizer": "AdamW",
        },
        "lora": profile.lora.model_dump(),
        "trainer": {"repo": "motionmirror smoke loop", "commit": None},
        "export_format": "safetensors",
        "peak_host_memory_gb": _peak_memory_gb(),
        "samples": [row["clip_id"] for row in rows],
        "dataset": {
            key: rows[0][key]
            for key in ("dataset_id", "dataset_version", "license", "license_scope")
        },
        "seed": seed,
        "steps_completed": step,
        "steps_planned": profile.steps,
        "resumed_from_step": resumed_from,
        "losses": losses,
        "adapter": {"path": adapter.name, "sha256": digest, "bytes": adapter.stat().st_size},
        "verification": verify_export(adapter, device, profile.video.height),
        "finished_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
