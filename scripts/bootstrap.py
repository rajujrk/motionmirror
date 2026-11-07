#!/usr/bin/env python3
"""Create local configuration. Never downloads weights or datasets, and never overwrites existing secrets."""

from __future__ import annotations

import base64
import hashlib
import platform
import re
import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".data" / "runtime"
PLACEHOLDER = "__GENERATE__"


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
        env_path.write_text(render_env((ROOT / ".env.example").read_text(), host_platform()))
        env_path.chmod(0o600)
        print("created .env with generated secrets")
    else:
        print(".env already exists, keeping it")
    values = parse_env(env_path.read_text())

    for directory, source, name in [
        ("rabbitmq", "configs/rabbitmq/definitions.template.json", "definitions.json"),
        ("redis", "configs/redis/users.template.acl", "users.acl"),
        ("seaweedfs", "configs/seaweedfs/s3.template.json", "s3.json"),
        ("keycloak", "infra/identity/realm-template.json", "motionmirror-realm.json"),
    ]:
        target = RUNTIME / directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_template((ROOT / source).read_text(), values))
    shutil.copy(ROOT / "configs/rabbitmq/rabbitmq.conf", RUNTIME / "rabbitmq" / "rabbitmq.conf")
    print(f"rendered local service configuration under {RUNTIME.relative_to(ROOT)}")
    print("no models or datasets were downloaded")


if __name__ == "__main__":
    main()
