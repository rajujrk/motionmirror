"""Training profiles. One schema for all of them, so smoke runs and real runs can't drift apart."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Video(_Strict):
    height: int
    width: int
    num_frames: int

    @model_validator(mode="after")
    def model_limits(self) -> Video:
        if self.height % 16 or self.width % 16:
            raise ValueError("height and width must be divisible by 16")
        if (self.num_frames - 1) % 4:
            raise ValueError("num_frames must be 4N+1")
        return self


class Lora(_Strict):
    rank: int
    target_modules: list[str]


class BaseModelPin(_Strict):
    training_id: str
    training_revision: str
    serving_id: str
    serving_revision: str


class Trainer(_Strict):
    repo: str
    commit: str
    license: str
    authors_note: str
    tested_gpu_count: int
    tested_gpu_memory_gb: int


class Profile(_Strict):
    schema_version: Literal[1]
    id: str
    kind: Literal["smoke", "real"]
    device: Literal["cuda", "mps", "cpu"]
    can_satisfy_training_gate: bool
    learning_rate: float
    video: Video
    lora: Lora
    steps: int | None = None
    checkpoint_every: int | None = None
    epochs: int | None = None
    dataset_repeat: int | None = None
    base_model: BaseModelPin | None = None
    trainer: Trainer | None = None
    disk_needed_gb: int | None = None

    @model_validator(mode="after")
    def kinds_stay_honest(self) -> Profile:
        if self.kind == "smoke":
            if self.can_satisfy_training_gate:
                raise ValueError("a smoke run can't satisfy the training gate")
            if not (self.steps and self.checkpoint_every):
                raise ValueError("a smoke profile needs steps and checkpoint_every")
        else:
            if self.device != "cuda":
                raise ValueError("a real run needs CUDA")
            if not (self.trainer and self.base_model and self.epochs and self.dataset_repeat):
                raise ValueError(
                    "a real profile needs a pinned trainer, a pinned base model, epochs and dataset_repeat"
                )
        return self


def load_profile(directory: Path, profile_id: str) -> Profile:
    path = directory / f"{profile_id}.yaml"
    if not path.exists():
        known = ", ".join(sorted(item.stem for item in directory.glob("*.yaml")))
        raise KeyError(f"Unknown training profile {profile_id!r}. Choose from: {known}")
    return Profile.model_validate(yaml.safe_load(path.read_text()))
