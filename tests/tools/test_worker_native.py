from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("worker_native", ROOT / "scripts" / "worker_native.py")
assert spec and spec.loader
worker_native = importlib.util.module_from_spec(spec)
sys.modules["worker_native"] = worker_native
spec.loader.exec_module(worker_native)

ENV = {
    "MM_PUBLIC_ORIGIN": "http://localhost:8088/",
    "MM_HOST_RABBIT_PORT": "55672",
    "MM_WORKER_WAN_CLIENT_SECRET": "wan-secret",
    "MM_WORKER_MOCK_CLIENT_SECRET": "mock-secret",
    "MM_WORKER_VALIDATE_CLIENT_SECRET": "validate-secret",
    "MM_RABBIT_CONSUMER_VALIDATE_PASSWORD": "validate-password",
    "MM_RABBIT_CONSUMER_WAN_PASSWORD": "wan-password",
    "MM_RABBIT_CONSUMER_MOCK_PASSWORD": "mock-password",
}


def test_with_mock_generation_real_masks_and_mock_output_use_separate_identities() -> None:
    workers = worker_native.worker_environments(ENV, "mock", "mps")
    assert set(workers) == {"validate", "preprocess", "generate"}
    assert workers["validate"]["MM_QUEUES"] == "motionmirror.validate.v1"
    assert workers["validate"]["MM_DEVICE_PROFILE"] == "cpu"
    assert "validate-password" in workers["validate"]["MM_AMQP_URL"]
    assert workers["preprocess"]["MM_DEVICE_PROFILE"] == "mps"
    assert workers["preprocess"]["MM_OIDC_CLIENT_ID"] == "motionmirror-worker-wan"
    assert workers["preprocess"]["MM_QUEUES"] == "motionmirror.preprocess.v1"
    assert workers["generate"]["MM_BACKEND"] == "mock"
    assert "mock-password" in workers["generate"]["MM_AMQP_URL"]
    assert "wan-password" not in workers["generate"]["MM_AMQP_URL"]


def test_with_real_generation_one_worker_takes_both_queues() -> None:
    workers = worker_native.worker_environments(ENV, "wan-animate", "mps")
    assert list(workers) == ["validate", "real"]
    assert "generate.wan-animate" in workers["real"]["MM_QUEUES"]


def test_workers_use_addresses_a_native_process_can_reach() -> None:
    for overrides in worker_native.worker_environments(ENV, "mock", "mps").values():
        assert "127.0.0.1" in overrides["MM_API_BASE_URL"]
        assert overrides["MM_OIDC_TOKEN_URL"].startswith("http://localhost:8088/auth/realms/")
        assert "@127.0.0.1:55672/" in overrides["MM_AMQP_URL"]


def test_the_env_file_parser_keeps_values_with_equals_signs(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# comment\n\nA=one\nB=two=three\n")
    assert worker_native.read_env_file(path) == {"A": "one", "B": "two=three"}


def test_the_runpod_template_keeps_weights_on_the_network_volume_and_scratch_on_the_container_disk() -> None:
    template = (ROOT / "infra" / "runpod" / "worker.env.template").read_text()
    assert "MM_MODEL_CACHE=/workspace/models" in template
    assert "MM_SCRATCH_DIR=/tmp/" in template
    assert "amqps://" in template, "a remote worker must use TLS to the broker"
    assert "MM_DEVICE_PROFILE=cuda" in template
