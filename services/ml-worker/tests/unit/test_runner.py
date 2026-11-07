"""The attempt supervisor, with a real child process and a fake control plane."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from motionmirror.worker.api_client import ApiUnavailable, Lease, LeaseLost
from motionmirror.worker.runner import AttemptRunner
from motionmirror.worker.settings import WorkerSettings


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
    api: FakeApi,
    tmp_path: Path,
    command: list[str],
    heartbeat: float = 0.05,
    max_seconds: float = 30,
    upload_retries: tuple[float, ...] = (),
) -> AttemptRunner:
    return AttemptRunner(
        api, tmp_path / "scratch", heartbeat, max_seconds, 2, child_command=command, upload_retry_delays=upload_retries
    )  # type: ignore[arg-type]


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
    assert Path(json.loads(seen.read_text())["adapter"]).read_bytes() == b"genuine"
    assert ("adapter_access", "adapter-1") in api.calls


@pytest.mark.asyncio
async def test_a_cached_adapter_isnt_downloaded_again_and_a_tampered_one_is(tmp_path: Path) -> None:
    from motionmirror.worker.runner import adapter_cache_dir, cached_adapters

    digest = hashlib.sha256(b"genuine").hexdigest()
    api = AdapterApi(b"genuine")
    command = child(tmp_path, "")
    await runner(api, tmp_path, command).run(adapter_lease(digest))
    assert api.calls.count(("adapter_access", "adapter-1")) == 1
    assert cached_adapters(tmp_path / "scratch") == [digest]

    await runner(api, tmp_path, command).run(adapter_lease(digest))
    assert api.calls.count(("adapter_access", "adapter-1")) == 1, "served from the cache"

    (adapter_cache_dir(tmp_path / "scratch") / f"{digest}.safetensors").write_bytes(b"tampered on disk")
    await runner(api, tmp_path, command).run(adapter_lease(digest))
    assert api.calls.count(("adapter_access", "adapter-1")) == 2, "the hash is checked on every use"
    assert (adapter_cache_dir(tmp_path / "scratch") / f"{digest}.safetensors").read_bytes() == b"genuine"


@pytest.mark.asyncio
async def test_the_adapter_cache_keeps_only_recent_adapters(tmp_path: Path) -> None:
    from motionmirror.worker.runner import ADAPTER_CACHE_SIZE, cached_adapters

    command = child(tmp_path, "")
    digests = []
    for index in range(ADAPTER_CACHE_SIZE + 2):
        content = f"adapter-{index}".encode()
        digests.append(hashlib.sha256(content).hexdigest())
        await runner(AdapterApi(content), tmp_path, command).run(adapter_lease(digests[-1]))
        time.sleep(0.01)
    held = cached_adapters(tmp_path / "scratch")
    assert len(held) == ADAPTER_CACHE_SIZE
    assert digests[-1] in held
    assert digests[0] not in held


def test_a_worker_advertises_the_adapters_it_holds(tmp_path: Path) -> None:
    from motionmirror.worker.__main__ import describe
    from motionmirror.worker.runner import adapter_cache_dir

    adapter_cache_dir(tmp_path).mkdir(parents=True)
    (adapter_cache_dir(tmp_path) / "abc.safetensors").write_bytes(b"x")
    settings = WorkerSettings(
        worker_id="w",
        backend="wan-animate",
        device_profile="cuda",
        api_base_url="http://api",
        oidc_token_url="http://id",
        oidc_client_id="c",
        oidc_client_secret="s",
        amqp_url="amqp://x",
        queues="q",
        scratch_dir=tmp_path,
    )
    assert describe(settings)["adapters"] == ["abc"]


@pytest.mark.asyncio
async def test_an_expired_signed_url_is_retried_with_a_fresh_one(tmp_path: Path) -> None:
    """Storage answers 403 once a URL expires. Each attempt asks for new URLs, so a retry isn't stuck with old ones."""
    expired = httpx.HTTPStatusError(
        "expired",
        request=httpx.Request("PUT", "https://write/x"),
        response=httpx.Response(403),
    )
    api = FakeApi()
    api.upload_error = expired
    body = """
out = workspace / "result.json"; out.write_text("{}")
output = {"name": "result.json", "path": str(out), "content_type": "application/json"}
(workspace / "attempt_result.json").write_text(json.dumps({"ok": True, "outputs": [output]}))
"""
    assert await runner(api, tmp_path, child(tmp_path, body)).run(LEASE) == "FAILED"
    assert ("fail", "STORAGE_ERROR", True) in api.calls


@pytest.mark.asyncio
async def test_stopping_an_attempt_reports_a_retryable_failure_at_once(tmp_path: Path) -> None:
    api = FakeApi()
    attempt = runner(api, tmp_path, child(tmp_path, "time.sleep(60)"))
    running = asyncio.create_task(attempt.run(LEASE))
    await asyncio.sleep(0.5)
    await attempt.stop_active()
    assert await asyncio.wait_for(running, timeout=10) == "FAILED"
    assert ("fail", "WORKER_CRASHED", True) in api.calls


@pytest.mark.asyncio
async def test_stopping_when_nothing_is_running_does_nothing(tmp_path: Path) -> None:
    await runner(FakeApi(), tmp_path, child(tmp_path, "")).stop_active()


def test_an_attempt_exits_by_itself_when_its_supervisor_is_killed(tmp_path: Path) -> None:
    """Otherwise it would hold the GPU for work nobody can report."""
    pid_file = tmp_path / "child.pid"
    grandchild = (
        "import os, time, pathlib\n"
        "from motionmirror.worker.child import exit_when_orphaned\n"
        "exit_when_orphaned(os.getppid(), 0.1)\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    supervisor = (
        "import subprocess, sys, time, pathlib\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
        f"while not pathlib.Path({str(pid_file)!r}).exists(): time.sleep(0.05)\n"
    )
    subprocess.run([sys.executable, "-c", supervisor], check=True, timeout=30)
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(pid, 9)
    pytest.fail("the attempt outlived its supervisor")


class FlakyStorageApi(FakeApi):
    """Storage fails a set number of times and then recovers, with each attempt asking for new URLs."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures_left = failures
        self.access_requests = 0

    async def artifact_access(self, lease: Lease, reads: list[str], writes: list[str]) -> dict[str, Any]:
        if writes:
            self.access_requests += 1
        return await super().artifact_access(lease, reads, writes)

    async def upload(self, url: str, path: Path, content_type: str) -> None:
        if self.failures_left:
            self.failures_left -= 1
            raise httpx.ConnectError("storage is down")
        await super().upload(url, path, content_type)


FINISHED_CHILD = """
out = workspace / "result.json"; out.write_text("{}")
output = {"name": "result.json", "path": str(out), "content_type": "application/json"}
(workspace / "attempt_result.json").write_text(json.dumps({"ok": True, "outputs": [output]}))
"""


@pytest.mark.asyncio
async def test_a_storage_blip_during_upload_keeps_the_finished_result(tmp_path: Path) -> None:
    api = FlakyStorageApi(failures=2)
    attempt = runner(api, tmp_path, child(tmp_path, FINISHED_CHILD), upload_retries=(0, 0, 0))
    assert await attempt.run(LEASE) == "SUCCEEDED"
    assert not [call for call in api.calls if call[0] == "fail"], "the attempt, and its GPU time, were not thrown away"
    assert api.access_requests == 3, "each try gets fresh URLs, in case the old ones expired"


@pytest.mark.asyncio
async def test_a_lasting_storage_outage_still_ends_in_a_retryable_failure(tmp_path: Path) -> None:
    api = FlakyStorageApi(failures=99)
    attempt = runner(api, tmp_path, child(tmp_path, FINISHED_CHILD), upload_retries=(0, 0))
    assert await attempt.run(LEASE) == "FAILED"
    assert ("fail", "STORAGE_ERROR", True) in api.calls


@pytest.mark.asyncio
async def test_heartbeats_continue_during_a_slow_upload(tmp_path: Path) -> None:
    """Otherwise a slow upload from a remote GPU loses its lease, and a finished result with it."""

    class SlowUpload(FakeApi):
        async def upload(self, url: str, path: Path, content_type: str) -> None:
            before = self.heartbeats
            await asyncio.sleep(0.3)
            self.during_upload = self.heartbeats - before

    api = SlowUpload()
    assert await runner(api, tmp_path, child(tmp_path, FINISHED_CHILD), heartbeat=0.05).run(LEASE) == "SUCCEEDED"
    assert api.during_upload >= 2


@pytest.mark.asyncio
async def test_heartbeats_continue_while_inputs_download(tmp_path: Path) -> None:
    """A remote worker fetching a long clip and an adapter can take longer than the lease."""

    class SlowDownload(FakeApi):
        async def download(self, url: str, target: Path) -> None:
            before = self.heartbeats
            await asyncio.sleep(0.3)
            self.during_download = self.heartbeats - before
            await super().download(url, target)

    api = SlowDownload()
    assert await runner(api, tmp_path, child(tmp_path, FINISHED_CHILD), heartbeat=0.05).run(LEASE) == "SUCCEEDED"
    assert api.during_download >= 2


@pytest.mark.asyncio
async def test_hashing_a_large_result_doesnt_stop_the_heartbeats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hashing blocks for as long as a multi-gigabyte file takes to read, so it runs off the event loop."""
    from motionmirror.worker import runner as runner_module

    real_hash = runner_module.sha256_file

    def slow_hash(path: Path) -> str:
        time.sleep(0.3)
        return real_hash(path)

    monkeypatch.setattr(runner_module, "sha256_file", slow_hash)

    class Counting(FakeApi):
        async def upload(self, url: str, path: Path, content_type: str) -> None:
            self.at_upload = self.heartbeats
            await super().upload(url, path, content_type)

        async def complete(
            self, lease: Lease, outputs: list[dict[str, Any]], result: dict[str, Any], effective: dict[str, Any]
        ) -> str:
            self.while_hashing = self.heartbeats - self.at_upload
            return await super().complete(lease, outputs, result, effective)

    api = Counting()
    assert await runner(api, tmp_path, child(tmp_path, FINISHED_CHILD), heartbeat=0.05).run(LEASE) == "SUCCEEDED"
    assert api.while_hashing >= 2


@pytest.mark.asyncio
async def test_a_heartbeat_outage_during_upload_doesnt_stop_it(tmp_path: Path) -> None:
    class NoControlPlane(FakeApi):
        async def heartbeat(self, lease: Lease) -> bool:
            raise ApiUnavailable("down")

        async def upload(self, url: str, path: Path, content_type: str) -> None:
            await asyncio.sleep(0.2)
            await super().upload(url, path, content_type)

    api = NoControlPlane()
    assert await runner(api, tmp_path, child(tmp_path, FINISHED_CHILD), heartbeat=0.05).run(LEASE) == "SUCCEEDED"
