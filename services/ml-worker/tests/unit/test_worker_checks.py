from __future__ import annotations

from typing import Any

import pytest

from motionmirror.cli.gpu_check import check_ffmpeg, run_checks
from motionmirror.preprocessing.models import require_gpu_session
from motionmirror.worker.__main__ import output_label
from motionmirror.worker.failures import AttemptFailure


@pytest.mark.parametrize(
    ("backend", "device", "label"),
    [
        ("mock", "cpu", "mock"),
        ("mock", "cuda", "mock"),
        ("wan-animate", "cuda", "real"),
        ("wan-animate", "mps", "experimental"),
        ("real-preprocess", "mps", "experimental"),
        ("real-preprocess", "cpu", "experimental"),
    ],
)
def test_only_a_cuda_worker_registers_as_real(backend: str, device: str, label: str) -> None:
    assert output_label(backend, device) == label


def test_all_checks_passing_passes() -> None:
    passed, results = run_checks({"one": lambda: {"ok": True}, "two": lambda: {"ok": True}})
    assert passed
    assert set(results) == {"one", "two"}


def test_one_failing_check_fails_the_whole_run() -> None:
    assert run_checks({"one": lambda: {"ok": True}, "two": lambda: {"ok": False}})[0] is False


def test_a_check_that_crashes_is_a_failure_with_a_reason_and_the_rest_still_run() -> None:
    def broken() -> dict[str, Any]:
        raise RuntimeError("no driver")

    passed, results = run_checks({"broken": broken, "fine": lambda: {"ok": True}})
    assert not passed
    assert "no driver" in results["broken"]["detail"]
    assert results["fine"]["ok"] is True


def test_this_machine_has_the_encoder_results_need() -> None:
    assert check_ffmpeg()["ok"] is True


class Session:
    def __init__(self, *providers: str) -> None:
        self.providers = list(providers)

    def get_providers(self) -> list[str]:
        return self.providers


def test_pose_on_a_cuda_worker_refuses_to_fall_back_to_the_cpu() -> None:
    with pytest.raises(AttemptFailure) as failure:
        require_gpu_session(Session("CPUExecutionProvider"), "cuda")
    assert failure.value.code == "UNSUPPORTED_CAPABILITY"


def test_pose_with_the_gpu_provider_passes_and_a_mac_never_needs_it() -> None:
    require_gpu_session(Session("CUDAExecutionProvider", "CPUExecutionProvider"), "cuda")
    require_gpu_session(Session("CPUExecutionProvider"), "mps")
