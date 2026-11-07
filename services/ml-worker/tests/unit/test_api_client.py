"""The control-plane client's retries, against a fake transport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from motionmirror.worker.api_client import ApiClient, Lease

GRANTED = {"attempt_id": "attempt-1", "attempt_no": 1, "fencing_token": 7, "request": {}}


def client(answer: Any) -> tuple[ApiClient, list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 300})
        bodies.append(json.loads(request.content))
        return answer(request, len(bodies))

    api = ApiClient("http://api", "http://idp/token", "worker", "secret", transport=httpx.MockTransport(handle))
    return api, bodies


@pytest.mark.asyncio
async def test_a_retried_acquire_is_the_same_request_so_it_can_get_its_lease_back() -> None:
    """The first response is lost after the API created the attempt. The retry must be recognisable as a repeat."""

    def lose_the_first(request: httpx.Request, count: int) -> httpx.Response:
        if count == 1:
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(200, json=GRANTED)

    api, bodies = client(lose_the_first)
    lease = await api.acquire("job", "event", 1, "worker-1")

    assert isinstance(lease, Lease)
    assert len(bodies) == 2
    assert bodies[0]["acquire_id"] == bodies[1]["acquire_id"]


@pytest.mark.asyncio
async def test_each_delivery_acquires_with_a_new_request_id() -> None:
    api, bodies = client(lambda request, count: httpx.Response(200, json=GRANTED))
    await api.acquire("job", "event", 1, "worker-1")
    await api.acquire("job", "event", 1, "worker-1")
    assert bodies[0]["acquire_id"] != bodies[1]["acquire_id"]
