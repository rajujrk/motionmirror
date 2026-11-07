"""Worker configuration from trusted deployment settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MM_", extra="ignore")

    worker_id: str
    backend: str = "mock"
    device_profile: str = "cpu"
    api_base_url: str
    oidc_token_url: str
    oidc_client_id: str
    oidc_client_secret: str
    amqp_url: str
    queues: str
    scratch_dir: Path = Path("/tmp/motionmirror-attempts")
    otlp_traces_endpoint: str | None = None
    heartbeat_seconds: float = 20.0
    max_attempt_seconds: float = 4 * 60 * 60
    terminate_grace_seconds: float = 20.0
    drain_seconds: float = 60.0
    http_timeout_seconds: float = 30.0
    max_generations: int = Field(default=1, description="Real generations at once on one GPU, independent of prefetch")

    @property
    def queue_names(self) -> list[str]:
        return [name.strip() for name in self.queues.split(",") if name.strip()]
