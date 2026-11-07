"""Apple Silicon spike: measure what really runs here. A failure is a valid result, and it gets recorded."""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

MEMORY_PRESSURE_POLL_SECONDS = 5
MAX_SWAP_GROWTH_BYTES = 6 * 1024**3
MAX_CRITICAL_POLLS = 6


def peak_rss_gb() -> float:
    """Peak resident memory of this process. macOS reports bytes, Linux kilobytes."""
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(value / (1024**3 if sys.platform == "darwin" else 1024**2), 2)


def swap_used_bytes() -> int:
    output = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, check=False).stdout
    used = output.split("used = ")[1].split()[0] if "used = " in output else "0M"
    return int(float(used.rstrip("MG")) * (1024**3 if used.endswith("G") else 1024**2))


def memory_pressure_level() -> int:
    output = subprocess.run(
        ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], capture_output=True, text=True, check=False
    )
    return int(output.stdout.strip() or 1)


class MemoryWatchdog:
    """Stops the run if the machine starts thrashing, so the spike can't leave a laptop unusable."""

    def __init__(self, on_trip: Any) -> None:
        self.baseline = swap_used_bytes()
        self.on_trip = on_trip
        self.tripped: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        critical = 0
        while not self._stop.wait(MEMORY_PRESSURE_POLL_SECONDS):
            growth = swap_used_bytes() - self.baseline
            critical = critical + 1 if memory_pressure_level() >= 4 else 0
            if growth > MAX_SWAP_GROWTH_BYTES:
                self.tripped = f"swap grew by {growth / 1024**3:.1f} GB"
            elif critical >= MAX_CRITICAL_POLLS:
                self.tripped = "sustained critical memory pressure"
            if self.tripped:
                self.on_trip(self.tripped)
                return

    def __enter__(self) -> MemoryWatchdog:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()


def _attempt(results: dict[str, Any], name: str, action: Any) -> None:
    started = time.monotonic()
    try:
        results[name] = {"ok": True, **(action() or {}), "seconds": round(time.monotonic() - started, 2)}
    except Exception as error:
        results[name] = {
            "ok": False,
            "error": f"{type(error).__name__}: {str(error)[:300]}",
            "seconds": round(time.monotonic() - started, 2),
        }
    print(name, json.dumps(results[name])[:300], flush=True)


def probe_dtypes(results: dict[str, Any]) -> None:
    import torch

    results["dtypes"] = {}
    for name in ("float16", "bfloat16", "float32", "float64"):
        try:
            tensor = torch.ones(2, 2, dtype=getattr(torch, name), device="mps")
            results["dtypes"][name] = bool((tensor @ tensor).sum().item() == 8)
        except Exception as error:
            results["dtypes"][name] = f"{type(error).__name__}: {str(error)[:120]}"


def mps_driver_gb(device: str) -> float | None:
    if device != "mps":
        return None
    import torch

    return round(torch.mps.driver_allocated_memory() / 1024**3, 2)


def iou(left: np.ndarray, right: np.ndarray) -> float:
    union = np.logical_or(left, right).sum()
    return float(np.logical_and(left, right).sum() / union) if union else 1.0


def seed_point(mask: np.ndarray) -> tuple[float, float]:
    """A point well inside the person, from the dataset's own mask, as a user's click would be."""
    import cv2

    distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 5)
    y, x = np.unravel_index(int(distance.argmax()), distance.shape)
    return float(x), float(y)


def run_tracking(clip_dir: Path, device: str, frames_limit: int, width: int, height: int) -> dict[str, Any]:
    from motionmirror.media.frames import read_frames
    from motionmirror.preprocessing.mapping import FramePrompt
    from motionmirror.preprocessing.models import Sam2Tracker, model_cache

    frames = read_frames(clip_dir / "video.mp4", width, height)[:frames_limit]
    truth = [frame[:, :, 0] > 127 for frame in read_frames(clip_dir / "dataset_mask.mp4", width, height)[:frames_limit]]
    x, y = seed_point(truth[0])
    tracker = Sam2Tracker(model_cache() / "sam2.1-hiera-small", device, "sam2.1-hiera-small")
    started = time.monotonic()
    masks = tracker.track(frames, [FramePrompt(0, [(x, y)], [1])])
    elapsed = time.monotonic() - started
    # Proof of where the work ran. Without PYTORCH_ENABLE_MPS_FALLBACK an unsupported op raises instead of moving.
    placement = {"model_device": str(next(tracker.model.parameters()).device), "mps_driver_gb": mps_driver_gb(device)}
    scores = [iou(mask.astype(bool), expected) for mask, expected in zip(masks, truth, strict=True)]
    return {
        "frames": len(frames),
        "size": f"{width}x{height}",
        **placement,
        "track_seconds": round(elapsed, 1),
        "seconds_per_frame": round(elapsed / len(frames), 3),
        "mean_iou_vs_dataset_mask": round(float(np.mean(scores)), 3),
        "min_iou_vs_dataset_mask": round(float(np.min(scores)), 3),
        "peak_rss_gb": peak_rss_gb(),
    }


def run_pose(clip_dir: Path, provider: str, frames_limit: int, width: int, height: int) -> dict[str, Any]:
    import onnxruntime

    from motionmirror.media.frames import read_frames
    from motionmirror.preprocessing.masks import bbox_from_mask
    from motionmirror.preprocessing.models import model_cache
    from motionmirror.preprocessing.wan_vendor.pose2d import ViTPose

    frames = read_frames(clip_dir / "video.mp4", width, height)[:frames_limit]
    truth = [
        (frame[:, :, 0] > 127).astype(np.uint8)
        for frame in read_frames(clip_dir / "dataset_mask.mp4", width, height)[:frames_limit]
    ]
    checkpoint = model_cache() / "wan-process/process_checkpoint/pose2d/vitpose_h_wholebody.onnx"
    model = ViTPose(str(checkpoint), device="cpu")
    if provider != "CPUExecutionProvider":
        model.session = onnxruntime.InferenceSession(
            str(checkpoint / "end2end.onnx"), providers=[provider, "CPUExecutionProvider"]
        )
    used = model.session.get_providers()
    started = time.monotonic()
    confidences = []
    for frame, mask in zip(frames, truth, strict=True):
        box = bbox_from_mask(mask)
        image, center, scale = model.preprocess(frame, np.array([*box, 1.0], dtype=np.float32))
        points = model(image[None], center[None], scale[None])
        confidences.append(float(points[0, :, 2].mean()))
    elapsed = time.monotonic() - started
    return {
        "providers_active": used,
        "frames": len(frames),
        "seconds_per_frame": round(elapsed / len(frames), 3),
        "mean_keypoint_confidence": round(float(np.mean(confidences)), 3),
        "peak_rss_gb": peak_rss_gb(),
    }


def run_gguf(output: Path, seconds_limit: int) -> dict[str, Any]:
    """Real Wan-Animate from the smallest GGUF at the smallest valid size, with a hard time limit and a watchdog."""
    import torch
    from diffusers import AutoencoderKLWan, GGUFQuantizationConfig, WanAnimatePipeline, WanAnimateTransformer3DModel
    from PIL import Image

    from motionmirror.preprocessing.models import model_cache

    cache = model_cache()
    stages: dict[str, Any] = {}

    def on_trip(reason: str) -> None:
        print(f"WATCHDOG: {reason}. Stopping to protect the machine.", flush=True)
        os.kill(os.getpid(), signal.SIGTERM)

    signal.signal(
        signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError(f"stopped at the {seconds_limit}s limit"))
    )
    signal.alarm(seconds_limit)
    with MemoryWatchdog(on_trip):
        started = time.monotonic()
        transformer = WanAnimateTransformer3DModel.from_single_file(
            str(cache / "gguf" / "Wan2.2-Animate-14B-Q2_K.gguf"),
            quantization_config=GGUFQuantizationConfig(compute_dtype=torch.float16),
            config=str(cache / "wan-animate-14b"),
            subfolder="transformer",
            torch_dtype=torch.float16,
        )
        stages["transformer_load_seconds"] = round(time.monotonic() - started, 1)
        stages["peak_rss_after_transformer_gb"] = peak_rss_gb()
        vae = AutoencoderKLWan.from_pretrained(
            str(cache / "wan-animate-14b"), subfolder="vae", torch_dtype=torch.float32
        )
        pipe = WanAnimatePipeline.from_pretrained(
            str(cache / "wan-animate-14b"), transformer=transformer, vae=vae, torch_dtype=torch.float16
        )
        pipe.enable_model_cpu_offload(device="mps")
        pipe.vae.enable_tiling()
        stages["pipeline_load_seconds"] = round(time.monotonic() - started, 1)
        stages["peak_rss_after_pipeline_gb"] = peak_rss_gb()
        print(json.dumps(stages), flush=True)

        frames = 9
        size = (256, 448)  # height, width: divisible by 16
        canvas = Image.new("RGB", (size[1], size[0]), (40, 40, 40))
        started = time.monotonic()
        result = pipe(
            image=canvas,
            pose_video=[canvas] * frames,
            face_video=[Image.new("RGB", (512, 512), (120, 90, 80))] * frames,
            background_video=[canvas] * frames,
            mask_video=[Image.new("L", (size[1], size[0]), 255)] * frames,
            prompt="a person",
            height=size[0],
            width=size[1],
            segment_frame_length=9,
            prev_segment_conditioning_frames=1,
            num_inference_steps=4,
            guidance_scale=1.0,
            mode="replace",
            generator=torch.Generator("cpu").manual_seed(0),
            output_type="np",
        )
        stages["generate_seconds"] = round(time.monotonic() - started, 1)
        stages["frames"] = frames
        stages["steps"] = 4
        stages["size"] = f"{size[1]}x{size[0]}"
        stages["output_shape"] = list(result.frames[0].shape)
        stages["peak_rss_gb"] = peak_rss_gb()
    signal.alarm(0)
    np.save(output / "gguf_output.npy", result.frames[0])
    return stages


STAGES = ("dtypes", "sam2-cpu", "sam2-mps", "pose-cpu", "pose-coreml", "gguf")


def run_stage(stage: str, args: argparse.Namespace, results: dict[str, Any]) -> None:
    width, height = 432, 768
    frames = args.frames
    if stage == "dtypes":
        probe_dtypes(results)
    elif stage == "sam2-cpu":
        _attempt(results, "sam2_cpu", lambda: run_tracking(args.clip_dir, "cpu", frames, width, height))
    elif stage == "sam2-mps":
        _attempt(results, "sam2_mps", lambda: run_tracking(args.clip_dir, "mps", frames, width, height))
    elif stage == "pose-cpu":
        _attempt(results, "pose_cpu", lambda: run_pose(args.clip_dir, "CPUExecutionProvider", frames, width, height))
    elif stage == "pose-coreml":
        _attempt(
            results, "pose_coreml", lambda: run_pose(args.clip_dir, "CoreMLExecutionProvider", frames, width, height)
        )
    elif stage == "gguf":
        _attempt(results, "wan_gguf_q2", lambda: run_gguf(args.out, args.gguf_seconds))


def sysctl(name: str) -> str:
    return subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, check=False).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--only", default="dtypes,sam2-cpu,sam2-mps,pose-cpu,pose-coreml")
    parser.add_argument("--gguf-seconds", type=int, default=7200)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    stages = args.only.split(",")
    unknown = set(stages) - set(STAGES)
    if unknown:
        parser.error(f"unknown stage: {sorted(unknown)}")

    if len(stages) > 1:
        # Peak memory only ever rises within a process, so each stage gets its own or the numbers would be meaningless.
        for stage in stages:
            command = [sys.executable, "-m", "motionmirror.cli.spike_mac", "--only", stage]
            command += ["--clip-dir", str(args.clip_dir), "--out", str(args.out), "--frames", str(args.frames)]
            command += ["--gguf-seconds", str(args.gguf_seconds)]
            subprocess.run(command, check=False)
        return 0

    import torch

    results: dict[str, Any] = {
        "machine": platform.machine(),
        "chip": sysctl("machdep.cpu.brand_string"),
        "memory_gb": int(sysctl("hw.memsize") or 0) // 1024**3,
        "macos": platform.mac_ver()[0],
        "torch": torch.__version__,
        "mps_available": torch.backends.mps.is_available(),
        "mps_fallback_env": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
    }
    run_stage(stages[0], args, results)
    path = args.out / f"spike_{stages[0]}_{args.frames}f.json"
    path.write_text(json.dumps(results, indent=2))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
