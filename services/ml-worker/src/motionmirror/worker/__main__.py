"""Worker entry point: python -m motionmirror.worker"""

from __future__ import annotations

import asyncio
import logging
import platform
import signal

from motionmirror.worker.api_client import ApiClient
from motionmirror.worker.consumer import TaskHandler, consume
from motionmirror.worker.runner import AttemptRunner, cached_adapters
from motionmirror.worker.settings import WorkerSettings
from motionmirror.worker.telemetry import configure_tracing

REGISTER_EVERY_SECONDS = 60


async def keep_registered(api: ApiClient, settings: WorkerSettings) -> None:
    while True:
        try:
            await api.register(describe(settings))
        except Exception as error:
            logging.getLogger(__name__).warning("worker registration failed: %s", type(error).__name__)
        await asyncio.sleep(REGISTER_EVERY_SECONDS)


def output_label(backend: str, device: str) -> str:
    """Only a CUDA worker's output can count as real. A Mac or CPU run is experimental, whatever it produces."""
    if backend in {"mock", "media-validate"}:
        return "mock" if backend == "mock" else "not-applicable"
    return "real" if device == "cuda" else "experimental"


def describe(settings: WorkerSettings) -> dict[str, object]:
    backends = {
        "mock": ["mock"],
        "wan-animate": ["wan-animate", "real-preprocess"],
        "real-preprocess": ["real-preprocess"],
        "media-validate": ["media-validate"],
    }
    return {
        "worker_id": settings.worker_id,
        "backends": backends.get(settings.backend, [settings.backend]),
        "device_type": settings.device_profile,
        "capabilities": {"output_label": output_label(settings.backend, settings.device_profile)},
        "adapters": cached_adapters(settings.scratch_dir),
        "runtime": {"python": platform.python_version(), "machine": platform.machine()},
    }


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = WorkerSettings()
    configure_tracing(settings.otlp_traces_endpoint, f"worker-{settings.backend}")
    api = ApiClient(
        settings.api_base_url,
        settings.oidc_token_url,
        settings.oidc_client_id,
        settings.oidc_client_secret,
        settings.http_timeout_seconds,
    )
    runner = AttemptRunner(
        api,
        settings.scratch_dir,
        settings.heartbeat_seconds,
        settings.max_attempt_seconds,
        settings.terminate_grace_seconds,
    )
    handler = TaskHandler(api, runner, settings.worker_id, settings.max_generations)
    registration = asyncio.create_task(keep_registered(api, settings))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signal_number, stop.set)
    try:
        await consume(settings, handler, stop=stop, drain_seconds=settings.drain_seconds)
    finally:
        registration.cancel()
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
