#!/usr/bin/env python3
"""Create local configuration. Never downloads weights or datasets, and never overwrites existing secrets."""

from __future__ import annotations

import base64
import hashlib
import os
import platform
import re
import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".data" / "runtime"
PLACEHOLDER = "__GENERATE__"
# Only salted hashes go to disk. Keycloak reads its secrets from env placeholders, and Redis and
# SeaweedFS render theirs into tmpfs inside the container (see infra/compose/compose.yaml).
DISK_RENDERED = [("configs/rabbitmq/definitions.template.json", "rabbitmq/definitions.json")]


def write_private(path: Path, content: str) -> None:
    """Write a file only its owner can read, with no window at wider permissions."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
    path.chmod(0o600)


def rabbit_password_hash(password: str, salt: bytes | None = None) -> str:
    """RabbitMQ's rabbit_password_hashing_sha256: base64(salt + sha256(salt + password))."""
    salt = salt if salt is not None else secrets.token_bytes(4)
    return base64.b64encode(salt + hashlib.sha256(salt + password.encode()).digest()).decode()


def host_platform() -> str:
    return "linux/arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "linux/amd64"


def render_env(example: str, machine_platform: str) -> str:
    lines = []
    for line in example.splitlines():
        if line.endswith(f"={PLACEHOLDER}"):
            line = line.replace(PLACEHOLDER, secrets.token_urlsafe(32))
        elif line.startswith("MM_PLATFORM="):
            line = f"MM_PLATFORM={machine_platform}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def missing_entries(example: str, existing: str, machine_platform: str) -> str:
    """New settings for an existing .env. Values already there are never touched, so secrets stay as they were."""
    have = parse_env(existing)
    added = [
        line
        for line in render_env(example, machine_platform).splitlines()
        if "=" in line and not line.startswith("#") and line.partition("=")[0].strip() not in have
    ]
    return "\n".join(added) + "\n" if added else ""


def parse_env(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def render_template(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.endswith("_HASH"):
            return rabbit_password_hash(values[name.removesuffix("_HASH")])
        return values[name]

    return re.sub(r"\$\{([A-Z0-9_]+)\}", replace, template)


def main() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        write_private(env_path, render_env((ROOT / ".env.example").read_text(), host_platform()))
        print("created .env with generated secrets")
    else:
        existing = env_path.read_text()
        added = missing_entries((ROOT / ".env.example").read_text(), existing, host_platform())
        if added:
            # Through write_private, so appending can't loosen the file's permissions.
            separator = "" if existing.endswith("\n") else "\n"
            write_private(env_path, existing + separator + added)
            names = [line.partition("=")[0] for line in added.splitlines()]
            print(f".env already exists, added new settings: {', '.join(names)}")
        else:
            print(".env already exists, keeping it")
    values = parse_env(env_path.read_text())

    for source, name in DISK_RENDERED:
        target = RUNTIME / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_template((ROOT / source).read_text(), values))
    shutil.copy(ROOT / "configs/rabbitmq/rabbitmq.conf", RUNTIME / "rabbitmq" / "rabbitmq.conf")
    print(f"rendered local service configuration under {RUNTIME.relative_to(ROOT)}")
    print("no models or datasets were downloaded")


if __name__ == "__main__":
    main()
