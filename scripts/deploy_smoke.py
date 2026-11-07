#!/usr/bin/env python3
"""After a deployment: is everything answering, and is everything private that should be? Changes nothing.

Usage: scripts/deploy_smoke.py --app https://app.example.com --worker https://workers.example.com
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

Fetch = Callable[[str], tuple[int, dict[str, str], bytes]]
TIMEOUT = 15


def fetch(url: str) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "motionmirror-deploy-smoke"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - the operator names the host
            return response.status, dict(response.headers), response.read(65536)
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read(65536)


@dataclass(frozen=True)
class Check:
    name: str
    url: str
    verify: Callable[[int, dict[str, str], bytes], bool]


def is_status(*expected: int) -> Callable[[int, dict[str, str], bytes], bool]:
    return lambda status, _headers, _body: status in expected


def discovery_has_https_issuer(status: int, _headers: dict[str, str], body: bytes) -> bool:
    return status == 200 and str(json.loads(body).get("issuer", "")).startswith("https://")


def page_has_security_headers(status: int, headers: dict[str, str], _body: bytes) -> bool:
    lower = {key.lower() for key in headers}
    return status == 200 and {"strict-transport-security", "content-security-policy"} <= lower


def checks(app: str, worker: str) -> list[Check]:
    closed = is_status(401, 403, 404)
    realm = "/auth/realms/motionmirror"
    return [
        Check("the site loads with its security headers", f"{app}/", page_has_security_headers),
        Check("identity answers with an HTTPS issuer", f"{app}{realm}/.well-known/openid-configuration", discovery_has_https_issuer),
        Check("the API refuses a signed-out visitor", f"{app}/api/v1/projects", is_status(401)),
        Check("the site doesn't serve the worker API", f"{app}/internal/v1/workers/register", closed),
        Check("the site doesn't serve the actuator", f"{app}/actuator/prometheus", closed),
        Check("the site doesn't serve identity administration", f"{app}/auth/admin/master/console/", closed),
        Check("the site doesn't publish the API schema", f"{app}/v3/api-docs", closed),
        Check("the worker host serves the worker API, and only to workers", f"{worker}/internal/v1/workers/register", is_status(401, 405)),
        Check("the worker host doesn't serve the app", f"{worker}/", is_status(404)),
        Check("the worker host doesn't serve the actuator", f"{worker}/actuator/prometheus", is_status(404)),
    ]


def run(app: str, worker: str, get: Fetch = fetch) -> list[tuple[str, bool, str]]:
    results = []
    for check in checks(app.rstrip("/"), worker.rstrip("/")):
        try:
            status, headers, body = get(check.url)
            results.append((check.name, check.verify(status, headers, body), f"HTTP {status}"))
        except Exception as error:  # noqa: BLE001 - a check that can't connect has failed, and the rest still run
            results.append((check.name, False, type(error).__name__))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--worker", required=True)
    args = parser.parse_args()
    results = run(args.app, args.worker)
    for name, passed, detail in results:
        print(f"{'ok  ' if passed else 'FAIL'} {name} ({detail})")
    return 0 if all(passed for _, passed, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
