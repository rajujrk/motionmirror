#!/usr/bin/env python3
"""Run the workers on this machine: real masks and poses on the Mac's GPU, and generation as configured.

Each worker uses its own credentials from .env, which never appear in output.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_DIR = ROOT / "services" / "ml-worker"
TOKEN_PATH = "/auth/realms/motionmirror/protocol/openid-connect/token"


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def amqp_url(env: dict[str, str], user: str, password_key: str) -> str:
    return f"amqp://{user}:{env[password_key]}@127.0.0.1:{env['MM_HOST_RABBIT_PORT']}/motionmirror"


def worker_environments(env: dict[str, str], generate: str, device: str) -> dict[str, dict[str, str]]:
    shared = {
        "MM_API_BASE_URL": f"http://127.0.0.1:{env.get('MM_HOST_API_PORT', '58081')}",
        "MM_OIDC_TOKEN_URL": env["MM_PUBLIC_ORIGIN"].rstrip("/") + TOKEN_PATH,
        "MM_SCRATCH_DIR": str(ROOT / ".data" / "runtime" / "worker-scratch"),
        "MM_MODEL_CACHE": str(ROOT / ".models"),
    }
    real = {
        **shared,
        "MM_OIDC_CLIENT_ID": "motionmirror-worker-wan",
        "MM_OIDC_CLIENT_SECRET": env["MM_WORKER_WAN_CLIENT_SECRET"],
        "MM_AMQP_URL": amqp_url(env, "mm-consumer-wan", "MM_RABBIT_CONSUMER_WAN_PASSWORD"),
        "MM_DEVICE_PROFILE": device,
    }
    workers = {}
    if generate == "mock":
        workers["preprocess"] = {
            **real,
            "MM_WORKER_ID": "native-preprocess",
            "MM_BACKEND": "real-preprocess",
            "MM_QUEUES": "motionmirror.preprocess.v1",
        }
        workers["generate"] = {
            **shared,
            "MM_WORKER_ID": "native-mock",
            "MM_BACKEND": "mock",
            "MM_DEVICE_PROFILE": "cpu",
            "MM_OIDC_CLIENT_ID": "motionmirror-worker-mock",
            "MM_OIDC_CLIENT_SECRET": env["MM_WORKER_MOCK_CLIENT_SECRET"],
            "MM_AMQP_URL": amqp_url(env, "mm-consumer-mock", "MM_RABBIT_CONSUMER_MOCK_PASSWORD"),
            "MM_QUEUES": "motionmirror.generate.mock.v1",
        }
    else:
        workers["real"] = {
            **real,
            "MM_WORKER_ID": "native-wan",
            "MM_BACKEND": "wan-animate",
            "MM_QUEUES": "motionmirror.preprocess.v1,motionmirror.generate.wan-animate.v1",
        }
    return workers


def command(module: str) -> list[str]:
    return ["uv", "run", "--project", str(WORKER_DIR), "--extra", "preprocess", "--extra", "wan", "python", "-m", module]


def run_checks(workers: dict[str, dict[str, str]]) -> int:
    worst = 0
    for name, overrides in workers.items():
        print(f"== {name}", flush=True)
        worst = max(worst, subprocess.run(command("motionmirror.cli.worker_check"), env={**os.environ, **overrides}, check=False).returncode)
    return worst


def run_workers(workers: dict[str, dict[str, str]]) -> int:
    processes = [subprocess.Popen(command("motionmirror.worker"), env={**os.environ, **overrides}) for overrides in workers.values()]

    def stop(*_: object) -> None:
        for process in processes:
            process.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"Started: {', '.join(workers)}. Press Ctrl-C to stop.", flush=True)
    codes = [process.wait() for process in processes]
    return max(codes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="test connectivity and permissions, then exit")
    parser.add_argument("--generate", default="mock", choices=["mock", "wan-animate"])
    parser.add_argument("--device", default="mps" if sys.platform == "darwin" else "cpu", choices=["mps", "cpu", "cuda"])
    args = parser.parse_args()
    env_file = ROOT / ".env"
    if not env_file.exists():
        print("No .env yet. Run: make up-mac-native", file=sys.stderr)
        return 2
    workers = worker_environments(read_env_file(env_file), args.generate, args.device)
    return run_checks(workers) if args.check else run_workers(workers)


if __name__ == "__main__":
    sys.exit(main())
