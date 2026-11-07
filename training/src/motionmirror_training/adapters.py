"""Operator commands for the adapter lifecycle. Everything goes through the API, under a person's own account."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

CLIENT_ID = "motionmirror-cli"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class AdapterCommandError(RuntimeError):
    """What went wrong, in the API's words where it gave any."""


def token_path(root: Path) -> Path:
    return root / ".data" / "runtime" / "operator-token"


def login(
    api_url: str,
    destination: Path,
    transport: httpx.BaseTransport | None = None,
    wait: float | None = None,
) -> None:
    """The device flow, so a password never reaches this tool. The token file is readable by this user only."""
    realm = f"{api_url.rstrip('/')}/auth/realms/motionmirror/protocol/openid-connect"
    with httpx.Client(transport=transport, timeout=30) as client:
        started = client.post(
            f"{realm}/auth/device", data={"client_id": CLIENT_ID, "scope": "openid operator"}
        )
        started.raise_for_status()
        device = started.json()
        print(
            f"Open {device['verification_uri_complete']} and sign in with an administrator account.",
            flush=True,
        )
        interval = float(device.get("interval", 5)) if wait is None else wait
        deadline = time.monotonic() + float(device.get("expires_in", 600))
        while time.monotonic() < deadline:
            time.sleep(interval)
            polled = client.post(
                f"{realm}/token",
                data={
                    "client_id": CLIENT_ID,
                    "grant_type": DEVICE_GRANT,
                    "device_code": device["device_code"],
                },
            )
            if polled.status_code == 200:
                destination.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w") as stream:
                    stream.write(polled.json()["access_token"])
                print(
                    f"Signed in. Token saved to {destination}. It expires soon, so sign in again when needed."
                )
                return
            error = polled.json().get("error")
            if error == "slow_down":
                interval += 5
            elif error != "authorization_pending":
                raise AdapterCommandError(f"Sign-in failed: {error}")
    raise AdapterCommandError("Sign-in timed out")


class AdminApi:
    def __init__(
        self, api_url: str, token: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
            transport=transport,
        )
        self._storage = httpx.Client(timeout=httpx.Timeout(60, write=1800), transport=transport)

    def _call(self, path: str, body: dict[str, Any] | None = None) -> Any:
        response = (
            self._client.post(path, json=body or {}) if body is not None else self._client.get(path)
        )
        if response.is_success:
            return response.json() if response.content else None
        try:
            problem = response.json()
        except ValueError:
            problem = {}
        raise AdapterCommandError(
            f"{problem.get('code', response.status_code)}: {problem.get('detail', 'request refused')}"
        )

    def register(
        self, run_dir: Path, name: str | None, scale: float, eval_manifest: Path | None
    ) -> dict[str, Any]:
        """Checks what can be checked here. The API checks it all again, and it holds the only real gate."""
        run = json.loads((run_dir / "run_manifest.json").read_text())
        if run.get("label") == "smoke":
            raise AdapterCommandError(
                "This is a smoke run on tiny random weights. It proves the plumbing and can't be registered"
            )
        if not run.get("adapter"):
            raise AdapterCommandError(f"{run_dir} has no exported adapter")
        adapter_file = run_dir / run["adapter"]["path"]
        target = self._call("/api/v1/admin/adapters/upload-target", {})
        with adapter_file.open("rb") as stream:
            uploaded = self._storage.put(
                target["url"],
                content=stream,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(adapter_file.stat().st_size),
                },
            )
        uploaded.raise_for_status()
        base = run["base_model"]
        return dict(
            self._call(
                "/api/v1/admin/adapters",
                {
                    "id": target["id"],
                    "name": name or run_dir.name,
                    "baseModelId": base["serving_id"],
                    "baseRevision": base["serving_revision"],
                    "sha256": run["adapter"]["sha256"],
                    "targetModules": run["lora"]["target_modules"],
                    "rank": run["lora"]["rank"],
                    "scale": scale,
                    "license": run["dataset"]["license"],
                    "licenseScope": run["license_scope"],
                    "datasetVersions": [f"{run['dataset']['id']}@{run['dataset']['version']}"],
                    "trainedOn": {
                        "trainer": run["trainer"],
                        "training_model": base["training_id"],
                        "plan": run["plan"],
                    },
                    "evalManifest": json.loads(eval_manifest.read_text())
                    if eval_manifest
                    else None,
                },
            )
        )

    def attach_evaluation(self, adapter_id: str, manifest: Path) -> dict[str, Any]:
        return dict(
            self._call(
                f"/api/v1/admin/adapters/{adapter_id}/evaluation", json.loads(manifest.read_text())
            )
        )

    def stage(self, adapter_id: str, preset: str) -> dict[str, Any]:
        return dict(self._call(f"/api/v1/admin/adapters/{adapter_id}/stage", {"presetId": preset}))

    def promote(self, adapter_id: str, preset: str) -> dict[str, Any]:
        return dict(
            self._call(f"/api/v1/admin/adapters/{adapter_id}/promote", {"presetId": preset})
        )

    def deprecate(self, adapter_id: str) -> dict[str, Any]:
        return dict(self._call(f"/api/v1/admin/adapters/{adapter_id}/deprecate", {}))

    def rollback(self, preset: str) -> dict[str, Any]:
        return dict(self._call(f"/api/v1/admin/presets/{preset}/rollback", {}))

    def list(self) -> dict[str, Any]:
        return dict(self._call("/api/v1/admin/adapters"))
