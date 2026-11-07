"""Acknowledgement ordering, with fakes for the broker and API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from motionmirror.worker.api_client import ApiUnavailable, Disposition, Lease, LeaseLost
from motionmirror.worker.consumer import TaskHandler

VALID_BODY = (
    Path(__file__).resolve().parents[4] / "packages/contracts/fixtures/task-envelope/valid/generate.json"
).read_bytes()


class FakeMessage:
    def __init__(self, body: bytes = VALID_BODY, log: list[str] | None = None) -> None:
        self.body = body
        self.log = log if log is not None else []

    async def ack(self) -> None:
        self.log.append("ack")

    async def reject(self, requeue: bool = False) -> None:
        self.log.append(f"reject(requeue={requeue})")

    async def nack(self, requeue: bool = True) -> None:
        self.log.append(f"nack(requeue={requeue})")


LEASE = Lease("job", "attempt", 1, 1, {"kind": "PREVIEW", "backend_id": "mock"})


class FakeApi:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.acquired = 0

    async def acquire(self, *args: Any) -> Any:
        self.acquired += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeRunner:
    def __init__(self, log: list[str], error: Exception | None = None) -> None:
        self.log = log
        self.error = error

    async def run(self, lease: Lease) -> str:
        self.log.append("run")
        if self.error:
            raise self.error
        self.log.append("outcome recorded")
        return "SUCCEEDED"


def handler(api: Any, runner: Any) -> TaskHandler:
    return TaskHandler(api, runner, "worker-1")


@pytest.mark.asyncio
async def test_a_message_is_acked_only_after_the_outcome_is_recorded() -> None:
    log: list[str] = []
    await handler(FakeApi(LEASE), FakeRunner(log)).handle(FakeMessage(log=log))
    assert log == ["run", "outcome recorded", "ack"]


@pytest.mark.asyncio
async def test_a_malformed_message_is_rejected_without_requeue() -> None:
    log: list[str] = []
    api = FakeApi(LEASE)
    await handler(api, FakeRunner(log)).handle(FakeMessage(b"not json", log))
    assert log == ["reject(requeue=False)"]
    assert api.acquired == 0


@pytest.mark.asyncio
async def test_an_unknown_schema_version_is_rejected_without_requeue() -> None:
    log: list[str] = []
    body = json.loads(VALID_BODY) | {"schema_version": 2}
    await handler(FakeApi(LEASE), FakeRunner(log)).handle(FakeMessage(json.dumps(body).encode(), log))
    assert log == ["reject(requeue=False)"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["DUPLICATE", "OBSOLETE", "CANCELLED"])
async def test_a_refused_delivery_is_acked_without_running(kind: str) -> None:
    log: list[str] = []
    await handler(FakeApi(Disposition(kind, "")), FakeRunner(log)).handle(FakeMessage(log=log))
    assert log == ["ack"]


@pytest.mark.asyncio
async def test_a_control_plane_outage_at_acquire_requeues() -> None:
    log: list[str] = []
    await handler(FakeApi(ApiUnavailable("down")), FakeRunner(log)).handle(FakeMessage(log=log))
    assert log == ["nack(requeue=True)"]


@pytest.mark.asyncio
async def test_losing_the_lease_acks_because_another_attempt_owns_the_job() -> None:
    log: list[str] = []
    await handler(FakeApi(LEASE), FakeRunner(log, LeaseLost("complete"))).handle(FakeMessage(log=log))
    assert log == ["run", "ack"]


@pytest.mark.asyncio
async def test_failing_to_record_the_outcome_does_not_ack() -> None:
    log: list[str] = []
    await handler(FakeApi(LEASE), FakeRunner(log, ApiUnavailable("down"))).handle(FakeMessage(log=log))
    assert log == ["run", "nack(requeue=True)"]
