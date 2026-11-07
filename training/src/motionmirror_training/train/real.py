"""The real LoRA run, wrapping the pinned trainer. Pending GPU validation: this has never been run.

Hardware and money are the owner's decision, so nothing starts without an explicit confirmation, a duration estimate
and the disk and memory checks. The trainer's own script says one 80 GB GPU can't train this model.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import resource
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from motionmirror_training.datasets.registry import DatasetEntry, Purpose, assert_training_allowed
from motionmirror_training.train.profiles import Profile
from motionmirror_training.train.smoke import train_samples

DATA_FILE_KEYS = (
    "video,animate_pose_video,animate_face_video,animate_inpaint_video,animate_mask_video"
)
EXTRA_INPUTS = (
    "input_image,animate_pose_video,animate_face_video,animate_inpaint_video,animate_mask_video"
)
LOCAL_FILES = (
    "models_t5_umt5-xxl-enc-bf16.pth",
    "Wan2.1_VAE.pth",
    "models_clip_open-clip-xlm-roberta-large-vit-huge-14.pth",
)


class PreflightError(RuntimeError):
    """A reason not to start spending money yet. The message says what to do about it."""


@dataclasses.dataclass(frozen=True)
class Hardware:
    gpu_count: int
    gpu_memory_gb: float
    free_disk_gb: float


def detect_hardware(path: Path) -> Hardware:
    import torch

    count = torch.cuda.device_count() if torch.cuda.is_available() else 0
    memory = torch.cuda.get_device_properties(0).total_memory / 1024**3 if count else 0.0
    return Hardware(count, memory, shutil.disk_usage(path).free / 1024**3)


def planned_steps(profile: Profile, sample_count: int, gpu_count: int) -> int:
    assert profile.epochs and profile.dataset_repeat
    per_epoch = sample_count * profile.dataset_repeat // max(gpu_count, 1)
    return per_epoch * profile.epochs


def preflight(
    profile: Profile,
    entry: DatasetEntry,
    sample_count: int,
    hardware: Hardware,
    seconds_per_step: float | None,
    confirmed: bool,
    transfer_confirmed: bool,
    accept_below_tested: bool,
) -> dict[str, Any]:
    """Returns the plan. Every refusal names the fix, and the licence is checked before the hardware."""
    if profile.kind != "real":
        raise PreflightError(f"{profile.id} is a smoke profile. Use make train-smoke")
    assert profile.trainer is not None
    assert_training_allowed(entry, Purpose.RESEARCH)
    if entry.transfer_requires_permission and not transfer_confirmed:
        raise PreflightError(
            f"The licence of {entry.id} limits transfer to outside parties. If this GPU is rented, get the authors' "
            "permission first, then pass --transfer-permission-confirmed. See docs/DATASETS.md"
        )
    if hardware.gpu_count == 0:
        raise PreflightError(
            "No CUDA GPU is visible. A real run needs one, and this never falls back to CPU or MPS"
        )
    tested = profile.trainer.tested_gpu_count * profile.trainer.tested_gpu_memory_gb
    available = hardware.gpu_count * hardware.gpu_memory_gb
    if available < tested and not accept_below_tested:
        raise PreflightError(
            f"This machine has {hardware.gpu_count} x {hardware.gpu_memory_gb:.0f} GB. The trainer's authors say: "
            f"{profile.trainer.authors_note!r}. Pass --accept-below-tested-hardware to try anyway, knowing it may fail"
        )
    needed = profile.disk_needed_gb or 0
    if hardware.free_disk_gb < needed:
        raise PreflightError(
            f"This run needs {needed} GB of disk and {hardware.free_disk_gb:.0f} GB is free"
        )
    if seconds_per_step is None:
        raise PreflightError(
            "No duration estimate. Run with --calibrate first, then pass --seconds-per-step"
        )
    steps = planned_steps(profile, sample_count, hardware.gpu_count)
    hours = steps * seconds_per_step / 3600
    plan = {
        "samples": sample_count,
        "gpu_count": hardware.gpu_count,
        "steps": steps,
        "seconds_per_step": seconds_per_step,
        "estimated_hours": round(hours, 2),
        "estimated_gpu_hours": round(hours * hardware.gpu_count, 2),
    }
    if not confirmed:
        raise PreflightError(
            f"Nothing started. Plan: {json.dumps(plan)}. Pass --confirm to start it"
        )
    return plan


def model_paths(model_dir: Path) -> str:
    """Local pinned files. The trainer's default downloads whatever is current, which a run record couldn't describe."""
    shards = sorted(str(path) for path in model_dir.glob("diffusion_pytorch_model*.safetensors"))
    if not shards:
        raise PreflightError(
            f"No model files in {model_dir}. Run make models-download PROFILE=training"
        )
    return json.dumps([shards, *(str(model_dir / name) for name in LOCAL_FILES)])


def write_accelerate_config(run_dir: Path, gpu_count: int) -> Path:
    """The trainer's own config at the pinned commit, with the process count set to this machine's."""
    config = {
        "compute_environment": "LOCAL_MACHINE",
        "debug": False,
        "deepspeed_config": {
            "gradient_accumulation_steps": 1,
            "offload_optimizer_device": "cpu",
            "offload_param_device": "cpu",
            "zero3_init_flag": False,
            "zero_stage": 2,
        },
        "distributed_type": "DEEPSPEED",
        "downcast_bf16": "no",
        "enable_cpu_affinity": False,
        "machine_rank": 0,
        "main_training_function": "main",
        "mixed_precision": "bf16",
        "num_machines": 1,
        "num_processes": gpu_count,
        "rdzv_backend": "static",
        "same_network": True,
        "tpu_env": [],
        "tpu_use_cluster": False,
        "tpu_use_sudo": False,
        "use_cpu": False,
    }
    path = run_dir / "accelerate_config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def build_command(
    profile: Profile,
    samples_dir: Path,
    model_dir: Path,
    trainer_dir: Path,
    run_dir: Path,
    accelerate_config: Path,
    epochs: int | None = None,
    dataset_repeat: int | None = None,
) -> list[str]:
    """The trainer's own LoRA script, plus the replacement-mode inputs its example leaves out."""
    return [
        "accelerate", "launch", "--config_file", str(accelerate_config),
        str(trainer_dir / "examples/wanvideo/model_training/train.py"),
        "--dataset_base_path", str(samples_dir),
        "--dataset_metadata_path", str(samples_dir / "metadata_train.csv"),
        "--data_file_keys", DATA_FILE_KEYS,
        "--height", str(profile.video.height),
        "--width", str(profile.video.width),
        "--num_frames", str(profile.video.num_frames),
        "--dataset_repeat", str(dataset_repeat or profile.dataset_repeat),
        "--model_paths", model_paths(model_dir),
        "--learning_rate", str(profile.learning_rate),
        "--num_epochs", str(epochs or profile.epochs),
        "--remove_prefix_in_ckpt", "pipe.dit.",
        "--output_path", str(run_dir / "checkpoints"),
        "--lora_base_model", "dit",
        "--lora_target_modules", ",".join(profile.lora.target_modules),
        "--lora_rank", str(profile.lora.rank),
        "--extra_inputs", EXTRA_INPUTS,
        "--use_gradient_checkpointing_offload",
        "--save_steps", str(profile.checkpoint_every),
        "--enable_csv_log",
        *resume_arguments(run_dir),
    ]  # fmt: skip


def newest_checkpoint(run_dir: Path) -> Path | None:
    """By time, not name: as text, step-500 sorts after step-1000."""
    done = (run_dir / "checkpoints").glob("*.safetensors")
    return max(done, key=lambda path: path.stat().st_mtime, default=None)


def resume_arguments(run_dir: Path) -> list[str]:
    """Picks up from the newest checkpoint, so a stopped rental doesn't throw away what it already paid for."""
    newest = newest_checkpoint(run_dir)
    return ["--resume_from_checkpoint", str(newest)] if newest else []


def run_manifest(
    profile: Profile,
    entry: DatasetEntry,
    samples_dir: Path,
    plan: dict[str, Any],
    command: list[str],
    adapter: Path | None,
) -> dict[str, Any]:
    assert profile.trainer is not None and profile.base_model is not None
    digest = hashlib.sha256(adapter.read_bytes()).hexdigest() if adapter else None
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    loss_logs = sorted(str(path) for path in adapter.parent.rglob("*.csv")) if adapter else []
    return {
        "hyperparameters": {
            "learning_rate": profile.learning_rate,
            "epochs": profile.epochs,
            "dataset_repeat": profile.dataset_repeat,
            "video": profile.video.model_dump(),
        },
        "peak_host_memory_gb": round(
            children / (1024**3 if sys.platform == "darwin" else 1024**2), 2
        ),
        "loss_logs": loss_logs,
        "export_format": "safetensors",
        "schema_version": 1,
        "label": "research" if entry.review.scope == "research_only" else "production-candidate",
        "license_scope": entry.review.scope,
        "profile": profile.id,
        "kind": profile.kind,
        "can_satisfy_training_gate": False,
        "training_gate_note": "A run alone never passes the gate. It needs the evaluation on a production-class worker.",
        "trainer": profile.trainer.model_dump(),
        "base_model": profile.base_model.model_dump(),
        "dataset": {
            "id": entry.id,
            "version": entry.version,
            "license": entry.license_name,
            "commercial_use_ok": entry.commercial_use_ok,
        },
        "samples_manifest_sha256": hashlib.sha256(
            (samples_dir / "manifest.json").read_bytes()
        ).hexdigest(),
        "plan": plan,
        "lora": profile.lora.model_dump(),
        "command": command,
        "adapter": {"path": adapter.name, "sha256": digest, "bytes": adapter.stat().st_size}
        if adapter
        else None,
        "finished_at": dt.datetime.now(dt.UTC).isoformat(),
    }


def run_real(
    profile: Profile,
    entry: DatasetEntry,
    samples_dir: Path,
    model_dir: Path,
    trainer_dir: Path,
    run_dir: Path,
    seconds_per_step: float | None,
    confirmed: bool,
    transfer_confirmed: bool,
    accept_below_tested: bool,
    hardware: Hardware | None = None,
    execute: Callable[[list[str], Path], int] | None = None,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    train = train_samples(samples_dir)
    found = hardware or detect_hardware(run_dir)
    plan = preflight(
        profile,
        entry,
        len(train),
        found,
        seconds_per_step,
        confirmed,
        transfer_confirmed,
        accept_below_tested,
    )
    command = build_command(
        profile,
        samples_dir,
        model_dir,
        trainer_dir,
        run_dir,
        write_accelerate_config(run_dir, found.gpu_count),
    )
    code = (execute or _execute)(command, trainer_dir)
    if code != 0:
        raise RuntimeError(f"the trainer exited with {code}. See {run_dir}")
    manifest = run_manifest(profile, entry, samples_dir, plan, command, newest_checkpoint(run_dir))
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def calibrate(
    profile: Profile,
    samples_dir: Path,
    model_dir: Path,
    trainer_dir: Path,
    run_dir: Path,
    hardware: Hardware | None = None,
    execute: Callable[[list[str], Path], int] | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """One short pass to measure a step. This costs GPU time too, so it's also an explicit command."""
    import time

    tick = clock or time.monotonic
    run_dir.mkdir(parents=True, exist_ok=True)
    found = hardware or detect_hardware(run_dir)
    if found.gpu_count == 0:
        raise PreflightError("No CUDA GPU is visible, so there's nothing to calibrate")
    train = train_samples(samples_dir)
    command = build_command(
        profile,
        samples_dir,
        model_dir,
        trainer_dir,
        run_dir,
        write_accelerate_config(run_dir, found.gpu_count),
        1,
        1,
    )
    started = tick()
    code = (execute or _execute)(command, trainer_dir)
    elapsed = tick() - started
    if code != 0:
        raise RuntimeError(
            f"calibration exited with {code}. A failure here is worth knowing before a full run"
        )
    steps = max(len(train) // max(found.gpu_count, 1), 1)
    result = {
        "seconds_per_step": round(elapsed / steps, 1),
        "steps": steps,
        "includes_model_load": True,
        "note": "Includes model loading, so it overstates the time per step on a short pass.",
    }
    (run_dir / "calibration.json").write_text(json.dumps(result, indent=2))
    return result


def _execute(command: list[str], cwd: Path) -> int:
    return subprocess.run(command, cwd=cwd, check=False).returncode
