from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deploy_smoke", ROOT / "scripts" / "deploy_smoke.py")
assert spec and spec.loader
deploy_smoke = importlib.util.module_from_spec(spec)
sys.modules["deploy_smoke"] = deploy_smoke
spec.loader.exec_module(deploy_smoke)

APP = "https://app.test"
WORKER = "https://workers.test"
SECURE = {"Strict-Transport-Security": "max-age=1", "Content-Security-Policy": "default-src 'self'"}


def healthy(url: str) -> tuple[int, dict[str, str], bytes]:
    if url == f"{APP}/":
        return 200, SECURE, b""
    if url.endswith("openid-configuration"):
        return 200, {}, json.dumps({"issuer": f"{APP}/auth/realms/motionmirror"}).encode()
    if url.endswith("/api/v1/projects") or url == f"{WORKER}/internal/v1/workers/register":
        return 401, {}, b""
    return 404, {}, b""


def failures(get) -> list[str]:  # type: ignore[no-untyped-def]
    return [name for name, passed, _ in deploy_smoke.run(APP, WORKER, get) if not passed]


def test_a_healthy_deployment_passes_every_check() -> None:
    assert failures(healthy) == []


def test_a_publicly_exposed_worker_api_fails() -> None:
    def exposed(url: str) -> tuple[int, dict[str, str], bytes]:
        return (200, {}, b"") if url == f"{APP}/internal/v1/workers/register" else healthy(url)

    assert failures(exposed) == ["the site doesn't serve the worker API"]


def test_public_metrics_fail() -> None:
    def exposed(url: str) -> tuple[int, dict[str, str], bytes]:
        return (200, {}, b"mm_jobs 1") if url.endswith("/actuator/prometheus") else healthy(url)

    assert set(failures(exposed)) == {"the site doesn't serve the actuator", "the worker host doesn't serve the actuator"}


def test_a_plain_http_issuer_fails() -> None:
    def insecure(url: str) -> tuple[int, dict[str, str], bytes]:
        if url.endswith("openid-configuration"):
            return 200, {}, json.dumps({"issuer": "http://app.test/auth"}).encode()
        return healthy(url)

    assert failures(insecure) == ["identity answers with an HTTPS issuer"]


def test_missing_security_headers_fail() -> None:
    def bare(url: str) -> tuple[int, dict[str, str], bytes]:
        return (200, {}, b"") if url == f"{APP}/" else healthy(url)

    assert failures(bare) == ["the site loads with its security headers"]


def test_a_host_that_cant_be_reached_fails_without_stopping_the_rest() -> None:
    def down(url: str) -> tuple[int, dict[str, str], bytes]:
        raise ConnectionError(url)

    results = deploy_smoke.run(APP, WORKER, down)
    assert len(results) == 10
    assert not any(passed for _, passed, _ in results)
    assert results[0][2] == "ConnectionError"
