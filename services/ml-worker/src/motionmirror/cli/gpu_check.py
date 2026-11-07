"""make smoke-gpu, first step: is this machine fit to run the model? Runs before any weights are needed."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any


def check_driver() -> dict[str, Any]:
    if shutil.which("nvidia-smi") is None:
        return {"ok": False, "detail": "nvidia-smi isn't available. Is the NVIDIA Container Toolkit set up?"}
    query = "name,driver_version,memory.total"
    done = subprocess.run(
        ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return {"ok": done.returncode == 0, "gpus": done.stdout.strip().splitlines()}


def check_torch() -> dict[str, Any]:
    import torch

    if not torch.cuda.is_available():
        return {"ok": False, "detail": "PyTorch can't see a CUDA GPU", "torch": torch.__version__}
    device = torch.device("cuda")
    sample = torch.ones(8, 8, device=device, dtype=torch.bfloat16)
    return {
        "ok": bool((sample @ sample).sum().item() == 512),
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "bf16_supported": torch.cuda.is_bf16_supported(),
        "gpu": torch.cuda.get_device_name(0),
    }


def check_onnx() -> dict[str, Any]:
    """Pose estimation would otherwise fall back to the CPU with only a warning, which ruins a benchmark quietly."""
    import onnxruntime

    providers = onnxruntime.get_available_providers()
    return {"ok": "CUDAExecutionProvider" in providers, "providers": providers}


def check_ffmpeg() -> dict[str, Any]:
    if shutil.which("ffmpeg") is None:
        return {"ok": False, "detail": "ffmpeg isn't installed"}
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30, check=False
    )
    return {"ok": "libx264" in done.stdout, "libx264": "libx264" in done.stdout}


CHECKS: dict[str, Callable[[], dict[str, Any]]] = {
    "driver": check_driver,
    "torch": check_torch,
    "onnxruntime": check_onnx,
    "ffmpeg": check_ffmpeg,
}


def run_checks(checks: dict[str, Callable[[], dict[str, Any]]] = CHECKS) -> tuple[bool, dict[str, Any]]:
    results: dict[str, Any] = {}
    for name, check in checks.items():
        try:
            results[name] = check()
        except Exception as error:
            results[name] = {"ok": False, "detail": f"{type(error).__name__}: {str(error)[:300]}"}
    return all(result["ok"] for result in results.values()), results


def main() -> int:
    passed, results = run_checks()
    print(json.dumps(results, indent=2))
    print("GPU check passed" if passed else "GPU check FAILED", file=sys.stderr)
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
