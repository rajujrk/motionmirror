"""Authenticated calls to the control plane. The worker has no database or cache access."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class LeaseLost(RuntimeError):
    """Another attempt owns this job now. Stop work and don't report a result."""


class ApiUnavailable(RuntimeError):
    """A transient failure talking to the control plane."""


@dataclass(frozen=True)
class Disposition:
    """The API said not to run this delivery."""

    kind: str
    detail: str


@dataclass(frozen=True)
class Lease:
    job_id: str
    attempt_id: str
    attempt_no: int
    fencing_token: int
    request: dict[str, Any]

    def fence(self) -> dict[str, Any]:
        return {"attempt_id": self.attempt_id, "fencing_token": self.fencing_token}


class ApiClient:
    RETRIES = 4

    def __init__(
        self,
        base_url: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token_url = token_url
        self._credentials = (client_id, client_secret)
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._storage = httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=600, write=600), transport=transport)
        self._token: str | None = None
        self._token_expires = 0.0

    async def close(self) -> None:
        await self._client.aclose()
        await self._storage.aclose()

    async def _bearer(self, force: bool = False) -> str:
        if force or self._token is None or time.monotonic() > self._token_expires:
            response = await self._client.post(
                self._token_url, data={"grant_type": "client_credentials"}, auth=self._credentials
            )
            response.raise_for_status()
            body = response.json()
            self._token = body["access_token"]
            self._token_expires = time.monotonic() + max(int(body.get("expires_in", 60)) - 30, 5)
        return str(self._token)

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        """Bounded retries for transient faults only. Every endpoint here is safe to repeat."""
        delay = 0.5
        refreshed = False
        for attempt in range(self.RETRIES):
            try:
                token = await self._bearer()
                response = await self._client.post(path, json=body, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as error:
                if attempt == self.RETRIES - 1:
                    raise ApiUnavailable(type(error).__name__) from error
            else:
                if response.status_code == 401 and not refreshed:
                    refreshed = True
                    await self._bearer(force=True)
                    continue
                if response.status_code < 500:
                    return response
                if attempt == self.RETRIES - 1:
                    raise ApiUnavailable(f"HTTP {response.status_code}")
            await asyncio.sleep(delay)
            delay *= 2
        raise ApiUnavailable("retries exhausted")

    async def register(self, body: dict[str, Any]) -> None:
        response = await self._post("/internal/v1/workers/register", body)
        response.raise_for_status()

    async def acquire(self, job_id: str, event_id: str, generation: int, worker_id: str) -> Lease | Disposition:
        response = await self._post(
            f"/internal/v1/jobs/{job_id}/acquire",
            {"event_id": event_id, "dispatch_generation": generation, "worker_id": worker_id},
        )
        if response.status_code == 409:
            body = response.json()
            return Disposition(body.get("disposition", "OBSOLETE"), body.get("detail", ""))
        response.raise_for_status()
        body = response.json()
        return Lease(job_id, body["attempt_id"], body["attempt_no"], body["fencing_token"], body["request"])

    async def _fenced(self, lease: Lease, action: str, body: dict[str, Any]) -> httpx.Response:
        response = await self._post(f"/internal/v1/jobs/{lease.job_id}/{action}", {**lease.fence(), **body})
        if response.status_code == 409:
            raise LeaseLost(action)
        response.raise_for_status()
        return response

    async def heartbeat(self, lease: Lease) -> bool:
        """Returns whether cancellation was requested."""
        return bool((await self._fenced(lease, "heartbeat", {})).json()["cancel_requested"])

    async def progress(
        self, lease: Lease, stage: str, current: int | None, total: int | None, unit: str | None
    ) -> None:
        await self._fenced(lease, "progress", {"stage": stage, "current": current, "total": total, "unit": unit})

    async def complete(
        self, lease: Lease, outputs: list[dict[str, Any]], result: dict[str, Any], effective: dict[str, Any]
    ) -> str:
        response = await self._fenced(
            lease, "complete", {"outputs": outputs, "result": result, "effective_settings": effective}
        )
        return str(response.json()["state"])

    async def fail(self, lease: Lease, code: str, detail: str, retryable: bool) -> str:
        response = await self._fenced(lease, "fail", {"code": code, "detail": detail[:1900], "retryable": retryable})
        return str(response.json()["state"])

    async def artifact_access(self, lease: Lease, reads: list[str], writes: list[str]) -> dict[str, Any]:
        return dict((await self._fenced(lease, "artifact-access", {"reads": reads, "writes": writes})).json())

    async def download(self, url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        async with self._storage.stream("GET", url) as response:
            response.raise_for_status()
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    output.write(chunk)

    async def upload(self, url: str, path: Path, content_type: str) -> None:
        async def body() -> Any:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk

        response = await self._storage.put(
            url, content=body(), headers={"Content-Type": content_type, "Content-Length": str(path.stat().st_size)}
        )
        response.raise_for_status()
