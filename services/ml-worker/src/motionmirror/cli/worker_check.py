"""make worker-check: can this worker reach everything it needs, and only that? Runs before any model loads.

A remote GPU worker can't use localhost or Docker service names, and a signed URL only works from where it was meant
to be used. These checks catch that for the price of a few requests instead of a failed job on a rented GPU.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import aio_pika
import httpx
from aiormq.exceptions import ChannelAccessRefused, ChannelNotFoundEntity

from motionmirror.worker.__main__ import describe
from motionmirror.worker.api_client import ApiClient
from motionmirror.worker.settings import WorkerSettings

OTHER_QUEUE = {
    "mock": "motionmirror.generate.wan-animate.v1",
    "wan-animate": "motionmirror.generate.mock.v1",
    "real-preprocess": "motionmirror.generate.mock.v1",
    "media-validate": "motionmirror.generate.mock.v1",
}
PROBE_BODY = b"motionmirror storage probe"


async def check_identity(api: ApiClient) -> dict[str, Any]:
    await api.authenticate()
    return {"ok": True}


async def check_registration(api: ApiClient, settings: WorkerSettings) -> dict[str, Any]:
    await api.register(describe(settings))
    return {"ok": True, "registered_as": settings.worker_id}


async def check_scope(api: ApiClient, settings: WorkerSettings) -> dict[str, Any]:
    """The credential must be refused a backend it wasn't issued for. Success here would be the problem."""
    response = await api.post("/internal/v1/workers/register", {**describe(settings), "backends": ["not-a-backend"]})
    return {"ok": response.status_code == 403, "status": response.status_code}


async def check_broker(settings: WorkerSettings) -> dict[str, Any]:
    connection = await aio_pika.connect(settings.amqp_url, timeout=15)
    async with connection:
        channel = await connection.channel()
        found = {}
        for name in settings.queue_names:
            queue = await channel.declare_queue(name, passive=True)
            found[name] = queue.declaration_result.consumer_count
    return {"ok": True, "queues_found": sorted(found)}


async def check_broker_limits(settings: WorkerSettings) -> dict[str, Any]:
    """This worker's account must not be able to consume another backend's queue."""
    other = OTHER_QUEUE.get(settings.backend)
    if other is None:
        return {"ok": True, "skipped": "no comparison queue for this backend"}
    connection = await aio_pika.connect(settings.amqp_url, timeout=15)
    async with connection:
        channel = await connection.channel()
        queue = await channel.get_queue(other, ensure=False)
        try:
            tag = await queue.consume(_ignore)
        except ChannelAccessRefused:
            return {"ok": True, "refused": other}
        except ChannelNotFoundEntity:
            return {"ok": False, "detail": f"{other} doesn't exist, so the limit wasn't tested"}
        await queue.cancel(tag)
    return {"ok": False, "detail": f"this account could consume {other}"}


async def _ignore(message: Any) -> None:
    await message.nack(requeue=True)


async def check_storage(api: ApiClient) -> dict[str, Any]:
    """Round trip through URLs the API signed, from where this worker actually is."""
    response = await api.post("/internal/v1/workers/storage-probe", {})
    response.raise_for_status()
    probe = response.json()
    async with httpx.AsyncClient(timeout=30) as client:
        written = await client.put(
            probe["write_url"], content=PROBE_BODY, headers={"Content-Type": probe["content_type"]}
        )
        written.raise_for_status()
        read = await client.get(probe["read_url"])
        read.raise_for_status()
    return {"ok": read.content == PROBE_BODY}


async def run_checks(settings: WorkerSettings) -> tuple[bool, dict[str, Any]]:
    api = ApiClient(
        settings.api_base_url,
        settings.oidc_token_url,
        settings.oidc_client_id,
        settings.oidc_client_secret,
        settings.http_timeout_seconds,
    )
    checks = {
        "identity": lambda: check_identity(api),
        "registration": lambda: check_registration(api, settings),
        "credential_scope": lambda: check_scope(api, settings),
        "broker": lambda: check_broker(settings),
        "broker_limits": lambda: check_broker_limits(settings),
        "storage": lambda: check_storage(api),
    }
    results: dict[str, Any] = {}
    try:
        for name, check in checks.items():
            try:
                results[name] = await check()
            except Exception as error:
                results[name] = {"ok": False, "detail": type(error).__name__}
    finally:
        await api.close()
    return all(result["ok"] for result in results.values()), results


def main() -> int:
    passed, results = asyncio.run(run_checks(WorkerSettings()))
    print(json.dumps(results, indent=2))
    print("Worker check passed" if passed else "Worker check FAILED", file=sys.stderr)
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
