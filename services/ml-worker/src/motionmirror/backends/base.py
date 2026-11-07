"""The backend interface. A new backend must not require changing the UI or the job system."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class BackendCapabilities:
    backend_id: str
    output_label: str  # mock | real | experimental
    device_type: str  # cuda | mps | cpu
    may_satisfy_acceptance: bool
    supports_negative_prompt: bool = False
    supports_multiple_references: bool = False
    continuation: str = "none"
    precisions: tuple[str, ...] = ()
    adapter_loading: bool = False
    loaded_adapters: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    code: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class GenerationRequest:
    job_id: str
    kind: str
    settings: dict[str, Any]
    inputs: dict[str, Any]
    source_path: Path
    primary_reference_path: Path | None = None
    control_paths: dict[str, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedInputs:
    request: GenerationRequest
    trim_start_seconds: float | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class GenerationResult:
    video_path: Path
    manifest_path: Path
    effective_settings: dict[str, Any] = field(default_factory=dict)


class JobContext:
    """What a backend can see of the running attempt: its own workspace, progress and cancellation."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        workspace.mkdir(parents=True, exist_ok=True)

    def progress(
        self, stage: str, current: int | None = None, total: int | None = None, unit: str | None = None
    ) -> None:
        """Report real work only. Never invent a percentage or an ETA."""

    def check_cancelled(self) -> None:
        """Raise at a safe point if this attempt should stop. The parent can also terminate the process."""


class VideoReplacementBackend(Protocol):
    def capabilities(self) -> BackendCapabilities: ...

    def validate(self, request: GenerationRequest) -> ValidationResult: ...

    def prepare(self, request: GenerationRequest) -> PreparedInputs: ...

    def generate(self, inputs: PreparedInputs, context: JobContext) -> GenerationResult: ...

    def release(self) -> None: ...
