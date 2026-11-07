#!/usr/bin/env python3
"""Diagnose this machine and the running stack. Reports only: it never installs or changes anything."""

from __future__ import annotations

import base64
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", str(ROOT / ".env"), "-f", str(ROOT / "infra/compose/compose.yaml")]
REQUIRED_ENCODERS = ("libx264", "aac")
CONTAINER_RUNTIMES = {"orbstack", "docker", "com.docke", "com.docker", "docker-pr", "vpnkit", "wslrelay"}
DEFAULT_MAX_ATTEMPT_MS = 4 * 60 * 60 * 1000
CLEANUP_MARGIN_MS = 30 * 60 * 1000


def normalize_arch(arch: str) -> str:
    return {"aarch64": "arm64", "x86_64": "amd64"}.get(arch.lower(), arch.lower())


def emulation_problem(host_arch: str, image_arch: str, service: str) -> str | None:
    if normalize_arch(host_arch) == normalize_arch(image_arch):
        return None
    return f"{service} runs a {image_arch} image on a {host_arch} host, which means emulation"


def missing_codecs(encoders_output: str) -> list[str]:
    return [codec for codec in REQUIRED_ENCODERS if not re.search(rf"\b{codec}\b", encoders_output)]


def redact(text: str) -> str:
    return re.sub(r"://([^:/@\s]+):[^@\s]+@", r"://\1:***@", text)


def deadline_problem(consumer_timeout_ms: int, max_attempt_ms: int = DEFAULT_MAX_ATTEMPT_MS) -> str | None:
    needed = max_attempt_ms + CLEANUP_MARGIN_MS
    if consumer_timeout_ms >= needed:
        return None
    return f"broker consumer timeout {consumer_timeout_ms} ms is below the required {needed} ms"


def port_conflicts(ports: dict[str, int], owner_of: Callable[[int], str | None]) -> list[str]:
    """Name host ports held by something other than the container runtime."""
    problems = []
    for name, port in ports.items():
        owner = owner_of(port)
        if owner and owner.lower() not in CONTAINER_RUNTIMES:
            problems.append(f"{name} port {port} is held by {owner}")
    return problems


@dataclass
class Report:
    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def ok(self, name: str, detail: str) -> None:
        self.rows.append(("ok", name, detail))

    def warn(self, name: str, detail: str) -> None:
        self.rows.append(("warn", name, detail))

    def fail(self, name: str, detail: str) -> None:
        self.rows.append(("FAIL", name, detail))

    @property
    def exit_code(self) -> int:
        return 1 if any(status == "FAIL" for status, _, _ in self.rows) else 0

    def print(self) -> None:
        for status, name, detail in self.rows:
            print(f"[{status:>4}] {name:<26} {redact(detail)}")


def run(command: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, str(error)
    return done.returncode, (done.stdout + done.stderr).strip()


def load_env() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and line[0] != "#")
    return {key: value for key, value in pairs}


def in_wsl() -> bool:
    return "microsoft" in platform.uname().release.lower()


def check_host(report: Report) -> None:
    system = platform.system()
    arch = normalize_arch(platform.machine())
    label = "WSL2" if in_wsl() else system
    if system == "Darwin":
        _, chip = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        _, memory = run(["sysctl", "-n", "hw.memsize"])
        report.ok("host", f"{label} {arch}, {chip}, {int(memory or 0) / 1024**3:.0f} GB unified memory")
        _, translated = run(["sysctl", "-n", "sysctl.proc_translated"])
        if translated.strip() == "1":
            report.fail("rosetta", "this shell runs under Rosetta; use a native arm64 terminal")
        else:
            report.ok("rosetta", "not in use")
    else:
        pages = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") if system == "Linux" else 0
        report.ok("host", f"{label} {arch}, {pages / 1024**3:.0f} GB memory")
    free = shutil.disk_usage(ROOT).free / 1024**3
    (report.ok if free > 20 else report.warn)("disk", f"{free:.0f} GB free on the project volume")


def check_tools(report: Report) -> None:
    for tool, command, required in [
        ("git", ["git", "--version"], True),
        ("uv", ["uv", "--version"], True),
        ("node", ["node", "--version"], True),
        ("pnpm", ["pnpm", "--version"], True),
    ]:
        code, output = run(command)
        if code == 0:
            report.ok(tool, output.splitlines()[0])
        else:
            (report.fail if required else report.warn)(tool, "not found")

    java_home = os.environ.get("JAVA_HOME", "")
    code, output = run([f"{java_home}/bin/java" if java_home else "java", "-version"])
    first = output.splitlines()[0] if output else "not found"
    (report.ok if code == 0 and '"21' in first else report.warn)("java 21", first)

    code, output = run(["ffmpeg", "-hide_banner", "-encoders"])
    if code != 0:
        report.warn("ffmpeg", "not found. Needed for native worker runs, not for containers")
    else:
        missing = missing_codecs(output)
        (report.fail if missing else report.ok)(
            "ffmpeg codecs", f"missing {missing}" if missing else ", ".join(REQUIRED_ENCODERS)
        )


def check_acceleration(report: Report) -> None:
    if platform.system() == "Darwin":
        python = ROOT / "services/ml-worker/.venv/bin/python"
        if python.exists():
            code, output = run([str(python), "-c", "import torch;print(torch.__version__, torch.backends.mps.is_available())"], 60)
            report.ok("mps", output.splitlines()[-1]) if code == 0 else report.warn("mps", "torch not installed in the worker venv")
        else:
            report.warn("mps", "worker venv not created. Run: cd services/ml-worker && uv sync --extra wan")
        report.warn("real inference", "needs a CUDA GPU. Use mock mode or a remote worker on this machine")
        return
    code, output = run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"])
    if code == 0:
        report.ok("nvidia gpu", output)
    else:
        report.warn("nvidia gpu", "none visible, so generation stays in mock mode or uses a remote worker")


def check_containers(report: Report, host_arch: str) -> None:
    code, output = run(["docker", "version", "--format", "{{.Server.Version}} {{.Server.Arch}}"])
    if code != 0:
        report.fail("container runtime", "docker daemon not reachable")
        return
    _, context = run(["docker", "context", "show"])
    report.ok("container runtime", f"{context} server {output}")

    code, output = run([*COMPOSE, "--profile", "lite", "--profile", "full", "ps", "--format", "{{.Service}}|{{.Image}}|{{.Health}}|{{.State}}"])
    if code != 0 or not output:
        report.warn("services", "none running. Start them with: make up-mac")
        return
    for line in output.splitlines():
        service, image, health, state = line.split("|")
        _, arch = run(["docker", "image", "inspect", image, "--format", "{{.Architecture}}"])
        problem = emulation_problem(host_arch, arch, service)
        if problem:
            report.fail(f"image {service}", problem)
        elif state != "running" or health in {"unhealthy", "starting"}:
            report.fail(f"service {service}", f"{state} {health}")
        else:
            report.ok(f"service {service}", f"{arch}, {health or state}")


def http_json(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def listening_process(port: int) -> str | None:
    code, output = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fc"], 5)
    if code != 0:
        return None
    names = [line[1:] for line in output.splitlines() if line.startswith("c")]
    return names[0] if names else None


def check_ports(report: Report, env: dict[str, str]) -> None:
    ports = {
        "http": int(env.get("MM_HTTP_PORT", 8088)),
        "postgres": int(env.get("MM_HOST_POSTGRES_PORT", 55432)),
        "rabbitmq": int(env.get("MM_HOST_RABBIT_PORT", 55672)),
        "redis": int(env.get("MM_HOST_REDIS_PORT", 56379)),
        "storage": int(env.get("MM_HOST_S3_PORT", 58333)),
    }
    problems = port_conflicts(ports, listening_process)
    for problem in problems:
        report.fail("port conflict", f"{problem}. Change it in .env")
    if not problems:
        report.ok("host ports", ", ".join(str(port) for port in ports.values()))


def check_identity(report: Report, env: dict[str, str]) -> None:
    origin = env.get("MM_PUBLIC_ORIGIN", "http://localhost:8088")
    realm = f"{origin}/auth/realms/motionmirror"
    try:
        discovery = http_json(f"{realm}/.well-known/openid-configuration")
    except (urllib.error.URLError, OSError) as error:
        report.warn("oidc discovery", f"{realm} not reachable ({error})")
        return
    if discovery.get("issuer") != realm:
        report.fail("oidc issuer", f"issuer {discovery.get('issuer')} differs from {realm}")
        return
    report.ok("oidc issuer", realm)
    try:
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        secret = base64.b64encode(f"motionmirror-worker-mock:{env['MM_WORKER_MOCK_CLIENT_SECRET']}".encode()).decode()
        token = http_json(discovery["token_endpoint"], body, {"Authorization": f"Basic {secret}"})
        claims = json.loads(base64.urlsafe_b64decode(token["access_token"].split(".")[1] + "=="))
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if "motionmirror-internal" in audiences:
            report.ok("worker token", "client credentials work, audience correct")
        else:
            report.fail("worker token", f"unexpected audience {audiences}")
    except (urllib.error.URLError, OSError, KeyError) as error:
        report.fail("worker token", str(error))


def check_broker(report: Report, env: dict[str, str]) -> None:
    code, output = run([*COMPOSE, "exec", "-T", "rabbitmq", "rabbitmqctl", "-q", "--formatter", "json", "list_policies", "-p", "motionmirror"])
    if code != 0:
        report.warn("broker policy", "rabbitmq not reachable")
        return
    try:
        policies = json.loads(output)
        definition = policies[0]["definition"]
        definition = json.loads(definition) if isinstance(definition, str) else definition
        problem = deadline_problem(int(definition["consumer-timeout"]))
    except (ValueError, KeyError, IndexError) as error:
        report.fail("broker deadline", f"cannot read consumer timeout: {error}")
        return
    if problem:
        report.fail("broker deadline", problem)
    else:
        report.ok("broker deadline", f"consumer timeout {definition['consumer-timeout']} ms")


def check_redis(report: Report, env: dict[str, str]) -> None:
    password = env.get("MM_REDIS_PASSWORD", "")
    code, output = run([*COMPOSE, "exec", "-T", "-e", f"REDISCLI_AUTH={password}", "redis", "redis-cli", "--user", "mm-api", "--no-auth-warning", "flushall"])
    if code == 127:
        report.warn("redis acl", "redis not reachable")
    elif "NOPERM" in output:
        report.ok("redis acl", "the application identity cannot run flushall")
    elif "no such service" in output or "not running" in output:
        report.warn("redis acl", "redis not running")
    else:
        report.fail("redis acl", f"expected NOPERM, got: {output[:80]}")


def main() -> int:
    report = Report()
    host_arch = normalize_arch(platform.machine())
    check_host(report)
    check_tools(report)
    check_acceleration(report)
    check_containers(report, host_arch)
    env = load_env()
    if env:
        check_ports(report, env)
        check_identity(report, env)
        check_broker(report, env)
        check_redis(report, env)
    else:
        report.warn("configuration", ".env missing. Run: make bootstrap")
    report.print()
    print("\nDoctor reports only. It changes nothing on this machine.")
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
