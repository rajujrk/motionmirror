"""The real tracker and pose model. Loaded only when a job needs them, from local pinned files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from motionmirror.preprocessing.mapping import FramePrompt
from motionmirror.worker.failures import AttemptFailure

FACE_SIZE = 512
FACE_BOX_SCALE = 1.3


def model_cache() -> Path:
    return Path(os.environ.get("MM_MODEL_CACHE", Path(__file__).resolve().parents[5] / ".models"))


def _require(path: Path) -> Path:
    if not path.exists():
        raise AttemptFailure(
            "MODEL_MISSING", f"{path.name} isn't installed. Run: make models-download PROFILE=preprocess"
        )
    return path


class Sam2Tracker:
    """SAM 2 through Transformers, from safetensors. Prompts are the user's marks, in model-canvas pixels."""

    def __init__(self, model_dir: Path, device: str, revision: str) -> None:
        import torch
        from transformers import Sam2VideoModel, Sam2VideoProcessor

        self.revision = revision
        self.device = device
        self.dtype = torch.bfloat16 if device == "cuda" else torch.float32
        self.model = Sam2VideoModel.from_pretrained(str(model_dir), local_files_only=True, use_safetensors=True)
        self.model = self.model.to(device, dtype=self.dtype).eval()
        self.processor = Sam2VideoProcessor.from_pretrained(str(model_dir), local_files_only=True)

    def track(self, frames: list[np.ndarray], prompts: list[FramePrompt]) -> list[np.ndarray]:
        import torch

        session = self.processor.init_video_session(
            video=frames,
            inference_device=self.device,
            video_storage_device="cpu",
            inference_state_device="cpu",
            processing_device="cpu",
            dtype=self.dtype,
        )
        height, width = frames[0].shape[:2]
        object_id = 1
        for prompt in prompts:
            arguments: dict[str, Any] = {}
            if prompt.points:
                arguments["input_points"] = [[[list(point) for point in prompt.points]]]
                arguments["input_labels"] = [[list(prompt.labels)]]
            if prompt.box is not None:
                arguments["input_boxes"] = [[list(prompt.box)]]
            if prompt.mask is not None:
                arguments["input_masks"] = prompt.mask.astype(bool)
            self.processor.add_inputs_to_inference_session(
                inference_session=session, frame_idx=prompt.frame, obj_ids=[object_id], **arguments
            )
        masks: dict[int, np.ndarray] = {}
        first = prompts[0].frame
        with torch.inference_mode():
            for reverse in (False, True):
                if reverse and first == 0:
                    continue
                for output in self.model.propagate_in_video_iterator(session, start_frame_idx=first, reverse=reverse):
                    processed = self.processor.post_process_masks(
                        [output.pred_masks], original_sizes=[[height, width]], binarize=True
                    )[0]
                    masks[output.frame_idx] = processed[0, 0].cpu().numpy().astype(np.uint8)
        empty = np.zeros((height, width), dtype=np.uint8)
        return [masks.get(index, empty) for index in range(len(frames))]


class VitPoseEstimator:
    """ViTPose-H wholebody, using boxes from the tracked mask rather than a detector that picks its own person."""

    def __init__(self, checkpoint: Path, device: str, revision: str) -> None:
        from motionmirror.preprocessing.wan_vendor.pose2d import ViTPose

        self.revision = revision
        self.model = ViTPose(str(checkpoint), device=device if device == "cuda" else "cpu")
        require_gpu_session(self.model.session, device)

    def estimate(self, frame: np.ndarray, box: tuple[int, int, int, int]) -> dict[str, Any]:
        from motionmirror.preprocessing.wan_vendor.pose2d_utils import load_pose_metas_from_kp2ds_seq

        height, width = frame.shape[:2]
        image, center, scale = self.model.preprocess(frame, np.array([*box, 1.0], dtype=np.float32))
        keypoints = self.model(image[None], center[None], scale[None])
        return dict(load_pose_metas_from_kp2ds_seq(keypoints, width=width, height=height)[0])

    def face_crop(self, frame: np.ndarray, meta: dict[str, Any]) -> np.ndarray | None:
        height, width = frame.shape[:2]
        box = face_box(np.asarray(meta["keypoints_face"])[:, :2], FACE_BOX_SCALE, (height, width))
        if box is None:
            return None
        x1, x2, y1, y2 = box
        crop = frame[y1:y2, x1:x2]
        return cv2.resize(crop, (FACE_SIZE, FACE_SIZE)) if crop.size else None

    def draw(self, meta: dict[str, Any], height: int, width: int) -> np.ndarray:
        from motionmirror.preprocessing.wan_vendor.human_visualization import draw_aapose_by_meta_new
        from motionmirror.preprocessing.wan_vendor.pose2d_utils import AAPoseMeta

        canvas = np.zeros((height, width, 3), dtype=np.uint8)
        return draw_aapose_by_meta_new(canvas, AAPoseMeta.from_humanapi_meta(meta))


def require_gpu_session(session: Any, device: str) -> None:
    """ONNX Runtime falls back to the CPU with only a warning, so a CUDA worker would silently crawl."""
    if device == "cuda" and "CUDAExecutionProvider" not in session.get_providers():
        raise AttemptFailure("UNSUPPORTED_CAPABILITY", "Pose estimation can't use the GPU on this worker")


def face_box(points: np.ndarray, scale: float, shape: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """The official crop: the face keypoints' box, widened, with extra room above for the forehead and hair."""
    height, width = shape
    pixels = points[1:] * (width, height)
    min_x, min_y = pixels.min(axis=0)
    max_x, max_y = pixels.max(axis=0)
    box_width, box_height = max_x - min_x, max_y - min_y
    if box_width <= 0 or box_height <= 0:
        return None
    area = box_width * box_height * scale
    new_width = np.sqrt(area * (box_width / box_height))
    new_height = np.sqrt(area * (box_height / box_width))
    delta_width = (new_width - box_width) / 2
    delta_height = (new_height - box_height) / 4
    return (
        int(max(min_x - delta_width, 0)),
        int(min(max_x + delta_width, width)),
        int(max(min_y - 3 * delta_height, 0)),
        int(min(max_y + delta_height, height)),
    )


def _device() -> str:
    from motionmirror.device import select_device

    return select_device(os.environ.get("MM_DEVICE_PROFILE", "cpu")).device  # type: ignore[arg-type]


def load_tracker() -> Sam2Tracker:
    device = _device()
    name = "sam2.1-hiera-large" if device == "cuda" else "sam2.1-hiera-small"
    return Sam2Tracker(_require(model_cache() / name), device, name)


def load_pose_estimator() -> VitPoseEstimator:
    checkpoint = _require(model_cache() / "wan-process" / "process_checkpoint" / "pose2d" / "vitpose_h_wholebody.onnx")
    return VitPoseEstimator(checkpoint, _device(), "vitpose_h_wholebody@wan2.2-animate-14b")
