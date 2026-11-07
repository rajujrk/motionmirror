"""Runs one leased attempt: fetch inputs, supervise the child, upload outputs, report the outcome."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from motionmirror.backends.mock import sha256_file
from motionmirror.media.probe import probe
from motionmirror.worker.api_client import ApiClient, ApiUnavailable, Lease, LeaseLost

log = logging.getLogger(__name__)

READS_BY_KIND: dict[str, list[str]] = {
    "VALIDATE": ["asset"],
    "PREPROCESS": ["source"],
    "PREVIEW": ["source", "primary_reference"],
    "FULL": ["source", "primary_reference"],
}
REAL_CONTROLS = [
    "preprocess:src_pose.mp4",
    "preprocess:src_face.mp4",
    "preprocess:src_bg.mp4",
    "preprocess:src_mask.mp4",
]


def reads_for(request: dict[str, Any]) -> list[str]:
    reads = list(READS_BY_KIND[request["kind"]])
    if request["kind"] in {"PREVIEW", "FULL"} and request["backend_id"] != "mock":
        reads += REAL_CONTROLS
    return reads


def local_name(read: str) -> str:
    return read.replace(":", "_")


class AdapterIntegrityError(RuntimeError):
    """The adapter's bytes don't match the hash recorded when it was registered."""


class AttemptRunner:
    def __init__(
        self,
        api: ApiClient,
        scratch_dir: Path,
        heartbeat_seconds: float,
        max_attempt_seconds: float,
        terminate_grace_seconds: float,
        child_command: list[str] | None = None,
    ) -> None:
        self._api = api
        self._scratch_dir = scratch_dir
        self._heartbeat_seconds = heartbeat_seconds
        self._max_attempt_seconds = max_attempt_seconds
        self._grace = terminate_grace_seconds
        self._child_command = child_command or [sys.executable, "-m", "motionmirror.worker.child"]

    async def run(self, lease: Lease) -> str:
        """Returns the job state the API recorded. Raises LeaseLost if another attempt took over."""
        workspace = self._scratch_dir / lease.attempt_id
        workspace.mkdir(parents=True, exist_ok=True)
        try:
            return await self._run(lease, workspace)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    async def _run(self, lease: Lease, workspace: Path) -> str:
        request = lease.request
        try:
            input_paths = await self._fetch_inputs(lease, workspace)
        except httpx.HTTPError as error:
            return await self._storage_failure(lease, "fetching inputs", error)
        except AdapterIntegrityError as error:
            return await self._api.fail(lease, "ADAPTER_INTEGRITY", str(error), False)
        spec_path = workspace / "spec.json"
        attempt_dir = workspace / "attempt"
        spec_path.write_text(
            json.dumps({"workspace": str(attempt_dir), "request": request, "input_paths": input_paths})
        )
        outcome = await self._supervise(lease, spec_path, attempt_dir)
        if outcome.get("cancelled"):
            return await self._api.fail(lease, "CANCELLED", "Cancelled by the user", False)
        if not outcome.get("ok"):
            return await self._api.fail(lease, outcome["code"], outcome["detail"], outcome["retryable"])

        await self._report(lease, "upload")
        try:
            outputs = await self._upload_outputs(lease, outcome["outputs"])
        except httpx.HTTPError as error:
            return await self._storage_failure(lease, "saving outputs", error)
        return await self._api.complete(
            lease, outputs, outcome.get("result", {}), outcome.get("effective_settings", {})
        )

    async def _storage_failure(self, lease: Lease, doing: str, error: httpx.HTTPError) -> str:
        """Report it now and let the API's retry policy decide, rather than waiting for the lease to expire."""
        log.warning("storage failed while %s for job %s: %s", doing, lease.job_id, type(error).__name__)
        return await self._api.fail(lease, "STORAGE_ERROR", f"Storage failed while {doing}", True)

    async def _fetch_inputs(self, lease: Lease, workspace: Path) -> dict[str, str]:
        reads = reads_for(lease.request)
        access = await self._api.artifact_access(lease, reads, [])
        paths: dict[str, str] = {}
        for name, url in access["reads"].items():
            target = workspace / "inputs" / local_name(name)
            await self._api.download(url, target)
            paths[name] = str(target)
        adapter = lease.request["inputs"].get("adapter")
        if adapter:
            paths["adapter"] = str(await self._fetch_adapter(lease, adapter, workspace))
        return paths

    async def _fetch_adapter(self, lease: Lease, adapter: dict[str, Any], workspace: Path) -> Path:
        """Checked before the child starts, so tampered weights never get near a model load."""
        access = await self._api.adapter_access(lease, adapter["id"])
        target = workspace / "inputs" / "adapter.safetensors"
        await self._api.download(access["url"], target)
        if sha256_file(target) != adapter["sha256"]:
            raise AdapterIntegrityError("The trained adapter doesn't match its recorded hash")
        return target

    async def _report(
        self, lease: Lease, stage: str, current: int | None = None, total: int | None = None, unit: str | None = None
    ) -> None:
        try:
            await self._api.progress(lease, stage, current, total, unit)
        except ApiUnavailable:
            log.warning("progress report failed for job %s", lease.job_id)

    async def _supervise(self, lease: Lease, spec_path: Path, attempt_dir: Path) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            *self._child_command, str(spec_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        progress = asyncio.create_task(self._forward_progress(lease, process))
        started = time.monotonic()
        try:
            while True:
                try:
                    await asyncio.wait_for(asyncio.shield(process.wait()), timeout=self._heartbeat_seconds)
                    break
                except TimeoutError:
                    pass
                if time.monotonic() - started > self._max_attempt_seconds:
                    await self._terminate(process)
                    return {
                        "ok": False,
                        "code": "ATTEMPT_TIMEOUT",
                        "detail": "The attempt ran past its limit",
                        "retryable": False,
                    }
                try:
                    if await self._api.heartbeat(lease):
                        await self._terminate(process)
                        return {"cancelled": True}
                except ApiUnavailable:
                    # The child keeps going. If the lease really expires, fencing protects the result.
                    log.warning("heartbeat failed for job %s", lease.job_id)
        except BaseException:
            await self._terminate(process)
            raise
        finally:
            await asyncio.gather(progress, return_exceptions=True)

        result_path = attempt_dir / "attempt_result.json"
        if not result_path.exists():
            return {
                "ok": False,
                "code": "WORKER_CRASHED",
                "detail": "The attempt process stopped unexpectedly",
                "retryable": True,
            }
        return dict(json.loads(result_path.read_text()))

    async def _forward_progress(self, lease: Lease, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while line := await process.stdout.readline():
            try:
                update = json.loads(line)
                await self._report(
                    lease, update["stage"], update.get("current"), update.get("total"), update.get("unit")
                )
            except (ValueError, KeyError):
                continue
            except LeaseLost:
                await self._terminate(process)
                raise

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=self._grace)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def _upload_outputs(self, lease: Lease, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = [output["name"] for output in outputs]
        access = await self._api.artifact_access(lease, [], names)
        references: list[dict[str, Any]] = []
        for output in outputs:
            path = Path(output["path"])
            write = access["writes"][output["name"]]
            # The URL is signed for this exact type. Sending another would fail the signature check.
            await self._api.upload(write["url"], path, write["content_type"])
            references.append(
                {
                    "name": output["name"],
                    "key": write["key"],
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                    "content_type": write["content_type"],
                    "media": _output_media(path, write["content_type"]),
                }
            )
        return references


def _output_media(path: Path, content_type: str) -> dict[str, Any]:
    if not content_type.startswith("video/"):
        return {}
    info = probe(path)
    return {
        "duration_seconds": info.duration_seconds,
        "width": info.display_width,
        "height": info.display_height,
        "audio_streams": info.audio_streams,
    }
