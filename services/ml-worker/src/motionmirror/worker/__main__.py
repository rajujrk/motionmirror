"""Worker entry point: python -m motionmirror.worker"""

from __future__ import annotations

import asyncio
import logging
import platform

from motionmirror.worker.api_client import ApiClient
from motionmirror.worker.consumer import TaskHandler, consume
from motionmirror.worker.runner import AttemptRunner
from motionmirror.worker.settings import WorkerSettings

REGISTER_EVERY_SECONDS = 60


async def keep_registered(api: ApiClient, body: dict[str, object]) -> None:
    while True:
        try:
            await api.register(body)
        except Exception as error:
            logging.getLogger(__name__).warning("worker registration failed: %s", type(error).__name__)
        await asyncio.sleep(REGISTER_EVERY_SECONDS)


def describe(settings: WorkerSettings) -> dict[str, object]:
    backends = {
        "mock": ["mock"],
        "wan-animate": ["wan-animate", "real-preprocess"],
        "real-preprocess": ["real-preprocess"],
    }
    return {
        "worker_id": settings.worker_id,
        "backends": backends.get(settings.backend, [settings.backend]),
        "device_type": settings.device_profile,
        "capabilities": {"output_label": "mock" if settings.backend == "mock" else "real"},
        "adapters": [],
        "runtime": {"python": platform.python_version(), "machine": platform.machine()},
    }


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = WorkerSettings()
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
    registration = asyncio.create_task(keep_registered(api, describe(settings)))
    try:
        await consume(settings, handler)
    finally:
        registration.cancel()
        await api.close()


if __name__ == "__main__":
    asyncio.run(main())
