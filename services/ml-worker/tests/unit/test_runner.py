"""The attempt supervisor, with a real child process and a fake control plane."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from motionmirror.worker.api_client import Lease, LeaseLost
from motionmirror.worker.runner import AttemptRunner


class FakeApi:
    def __init__(self, cancel_after: int | None = None, lease_lost: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.heartbeats = 0
        self.cancel_after = cancel_after
        self.lease_lost = lease_lost
        self.uploaded_types: list[str] = []
        self.upload_error: Exception | None = None

    async def artifact_access(self, lease: Lease, reads: list[str], writes: list[str]) -> dict[str, Any]:
        return {
            "reads": {name: f"https://read/{name}" for name in reads},
            "writes": {
                name: {
                    "key": f"attempts/{lease.attempt_id}/{name}",
                    "url": f"https://write/{name}",
                    "content_type": "signed/type",
                }
                for name in writes
            },
        }

    async def download(self, url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"input")

    async def upload(self, url: str, path: Path, content_type: str) -> None:
        self.calls.append(("upload", path.name))
        self.uploaded_types.append(content_type)
        if self.upload_error:
            raise self.upload_error

    async def heartbeat(self, lease: Lease) -> bool:
        self.heartbeats += 1
        if self.lease_lost:
            raise LeaseLost("heartbeat")
        return self.cancel_after is not None and self.heartbeats >= self.cancel_after

    async def progress(self, lease: Lease, stage: str, *args: Any) -> None:
        self.calls.append(("progress", stage))

    async def complete(
        self, lease: Lease, outputs: list[dict[str, Any]], result: dict[str, Any], effective: dict[str, Any]
    ) -> str:
        self.calls.append(("complete", outputs))
        return "SUCCEEDED"

    async def fail(self, lease: Lease, code: str, detail: str, retryable: bool) -> str:
        self.calls.append(("fail", code, retryable))
        return "FAILED"


LEASE = Lease("job", "attempt-1", 1, 1, {"kind": "PREVIEW", "backend_id": "mock", "inputs": {}})


def child(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "fake_child.py"
    script.write_text(
        "import json, sys, time, pathlib\n"
        "spec = json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
        "workspace = pathlib.Path(spec['workspace']); workspace.mkdir(parents=True, exist_ok=True)\n" + body
    )
    return [sys.executable, str(script)]


def runner(
    api: FakeApi, tmp_path: Path, command: list[str], heartbeat: float = 0.05, max_seconds: float = 30
) -> AttemptRunner:
    return AttemptRunner(api, tmp_path / "scratch", heartbeat, max_seconds, 2, child_command=command)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_successful_attempt_uploads_then_completes(tmp_path: Path) -> None:
    api = FakeApi()
    command = child(
        tmp_path,
        """
out = workspace / "result.json"; out.write_text("{}")
print(json.dumps({"stage": "generate", "current": 1, "total": 2, "unit": "segments"}), flush=True)
output = {"name": "result.json", "path": str(out), "content_type": "application/json"}
(workspace / "attempt_result.json").write_text(json.dumps({"ok": True, "outputs": [output]}))
""",
    )
    assert await runner(api, tmp_path, command).run(LEASE) == "SUCCEEDED"
    names = [call[0] for call in api.calls]
    assert names.index("upload") < names.index("complete")
    assert ("progress", "generate") in api.calls
    assert ("progress", "upload") in api.calls


@pytest.mark.asyncio
async def test_heartbeats_continue_while_the_child_is_busy(tmp_path: Path) -> None:
    api = FakeApi()
    command = child(
        tmp_path,
        """
time.sleep(0.5)
(workspace / "attempt_result.json").write_text(json.dumps({"ok": True, "outputs": []}))
""",
    )
    await runner(api, tmp_path, command).run(LEASE)
    assert api.heartbeats >= 3


@pytest.mark.asyncio
async def test_cancellation_terminates_the_child_and_reports_it(tmp_path: Path) -> None:
    api = FakeApi(cancel_after=2)
    command = child(tmp_path, "time.sleep(60)\n")
    await runner(api, tmp_path, command).run(LEASE)
    assert ("fail", "CANCELLED", False) in api.calls


@pytest.mark.asyncio
async def test_a_lost_lease_stops_the_child_and_reports_nothing(tmp_path: Path) -> None:
    api = FakeApi(lease_lost=True)
    command = child(tmp_path, "time.sleep(60)\n")
    with pytest.raises(LeaseLost):
        await runner(api, tmp_path, command).run(LEASE)
    assert not [call for call in api.calls if call[0] in {"complete", "fail"}]


@pytest.mark.asyncio
async def test_a_crashed_child_becomes_a_retryable_failure(tmp_path: Path) -> None:
    api = FakeApi()
    command = child(tmp_path, "sys.exit(9)\n")
    await runner(api, tmp_path, command).run(LEASE)
    assert ("fail", "WORKER_CRASHED", True) in api.calls


@pytest.mark.asyncio
async def test_a_reported_failure_keeps_its_code(tmp_path: Path) -> None:
    api = FakeApi()
    command = child(
        tmp_path,
        """
failure = {"ok": False, "code": "CUDA_OOM", "detail": "out of memory", "retryable": False}
(workspace / "attempt_result.json").write_text(json.dumps(failure))
""",
    )
    await runner(api, tmp_path, command).run(LEASE)
    assert ("fail", "CUDA_OOM", False) in api.calls


@pytest.mark.asyncio
async def test_a_run_past_the_limit_is_stopped(tmp_path: Path) -> None:
    api = FakeApi()
    command = child(tmp_path, "time.sleep(60)\n")
    await runner(api, tmp_path, command, max_seconds=0.1).run(LEASE)
    assert ("fail", "ATTEMPT_TIMEOUT", False) in api.calls


@pytest.mark.asyncio
async def test_the_workspace_is_removed_afterwards(tmp_path: Path) -> None:
    api = FakeApi()
    command = child(
        tmp_path,
        """
(workspace / "attempt_result.json").write_text(json.dumps({"ok": True, "outputs": []}))
""",
    )
    await runner(api, tmp_path, command).run(LEASE)
    assert not (tmp_path / "scratch" / "attempt-1").exists()


@pytest.mark.asyncio
async def test_the_real_child_validates_media_end_to_end(tmp_path: Path, clip_with_audio: Path) -> None:
    api = FakeApi()

    async def download(url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(clip_with_audio.read_bytes())

    api.download = download  # type: ignore[method-assign]
    lease = Lease(
        "job",
        "attempt-2",
        1,
        1,
        {
            "kind": "VALIDATE",
            "backend_id": "mock",
            "settings": {},
            "inputs": {"role": "DRIVING_VIDEO", "max_seconds": 30},
        },
    )
    real = AttemptRunner(api, tmp_path / "scratch", 0.5, 60, 2)  # type: ignore[arg-type]
    assert await real.run(lease) == "SUCCEEDED"
    completed = [call for call in api.calls if call[0] == "complete"]
    assert completed
    assert json.dumps(completed[0][1]) == "[]"


SUCCESS_WITH_OUTPUT = """
out = workspace / "result.json"; out.write_text("{}")
(workspace / "attempt_result.json").write_text(json.dumps({"ok": True, "outputs": [
    {"name": "result.json", "path": str(out), "content_type": "application/json"}]}))
"""


@pytest.mark.asyncio
async def test_an_upload_uses_the_content_type_the_url_was_signed_for(tmp_path: Path) -> None:
    api = FakeApi()
    await runner(api, tmp_path, child(tmp_path, SUCCESS_WITH_OUTPUT)).run(LEASE)
    assert api.uploaded_types == ["signed/type"]


@pytest.mark.asyncio
async def test_a_storage_failure_on_upload_is_reported_at_once_as_retryable(tmp_path: Path) -> None:
    api = FakeApi()
    api.upload_error = httpx.ConnectError("storage down")
    assert await runner(api, tmp_path, child(tmp_path, SUCCESS_WITH_OUTPUT)).run(LEASE) == "FAILED"
    assert ("fail", "STORAGE_ERROR", True) in api.calls
    assert not [call for call in api.calls if call[0] == "complete"]


@pytest.mark.asyncio
async def test_a_storage_failure_on_download_is_reported_at_once_as_retryable(tmp_path: Path) -> None:
    api = FakeApi()

    async def failing_download(url: str, target: Path) -> None:
        raise httpx.ConnectError("storage down")

    api.download = failing_download  # type: ignore[method-assign]
    assert await runner(api, tmp_path, child(tmp_path, SUCCESS_WITH_OUTPUT)).run(LEASE) == "FAILED"
    assert ("fail", "STORAGE_ERROR", True) in api.calls


class AdapterApi(FakeApi):
    """The stored bytes hash to `stored`, whatever the job's snapshot expects."""

    def __init__(self, stored: bytes) -> None:
        super().__init__()
        self.stored = stored
        self.failures: list[tuple[str, bool]] = []

    async def adapter_access(self, lease: Lease, adapter_id: str) -> dict[str, Any]:
        self.calls.append(("adapter_access", adapter_id))
        return {"url": "https://read/adapter", "sha256": "ignored", "scale": 1.0}

    async def download(self, url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.stored if url == "https://read/adapter" else b"input")

    async def fail(self, lease: Lease, code: str, detail: str, retryable: bool) -> str:
        self.failures.append((code, retryable))
        return "FAILED"


def adapter_lease(sha256: str) -> Lease:
    adapter = {"id": "adapter-1", "name": "a", "sha256": sha256, "scale": 1.0, "license_scope": "research_only"}
    request = {
        "job_id": "job",
        "kind": "PREVIEW",
        "backend_id": "mock",
        "settings": {},
        "inputs": {"adapter": adapter},
    }
    return Lease("job", "attempt-1", 1, 1, request)


@pytest.mark.asyncio
async def test_a_tampered_adapter_stops_the_job_before_any_child_starts(tmp_path: Path) -> None:
    api = AdapterApi(b"tampered")
    marker = tmp_path / "child_ran"
    command = child(tmp_path, f"open({str(marker)!r}, 'w').close()")
    attempt = runner(api, tmp_path, command)
    assert await attempt.run(adapter_lease(hashlib.sha256(b"genuine").hexdigest())) == "FAILED"
    assert api.failures == [("ADAPTER_INTEGRITY", False)]
    assert not marker.exists()


@pytest.mark.asyncio
async def test_a_genuine_adapter_reaches_the_child_as_a_local_file(tmp_path: Path) -> None:
    api = AdapterApi(b"genuine")
    seen = tmp_path / "seen.json"
    body = f"""
spec = json.loads(open(sys.argv[1]).read())
open({str(seen)!r}, 'w').write(json.dumps(spec['input_paths']))
"""
    attempt = runner(api, tmp_path, child(tmp_path, body))
    await attempt.run(adapter_lease(hashlib.sha256(b"genuine").hexdigest()))
    assert json.loads(seen.read_text())["adapter"].endswith("adapter.safetensors")
    assert ("adapter_access", "adapter-1") in api.calls
