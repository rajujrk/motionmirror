"""A tiny random-weight WanAnimatePipeline for contract tests and smoke runs.

It runs the real pinned pipeline code with no downloads. Output carries no meaning, so it must
never be used as evidence of quality.
"""

from __future__ import annotations

from typing import Any

import torch
from diffusers import (
    AutoencoderKLWan,
    FlowMatchEulerDiscreteScheduler,
    WanAnimatePipeline,
    WanAnimateTransformer3DModel,
)
from PIL import Image
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import (
    CLIPImageProcessor,
    CLIPVisionConfig,
    CLIPVisionModelWithProjection,
    PreTrainedTokenizerFast,
    T5Config,
    T5EncoderModel,
)

TINY_FACE_SIZE = 16
TEXT_DIM = 32


def build_tiny_transformer() -> WanAnimateTransformer3DModel:
    torch.manual_seed(0)
    return WanAnimateTransformer3DModel(
        patch_size=(1, 2, 2),
        num_attention_heads=2,
        attention_head_dim=12,
        in_channels=36,
        latent_channels=16,
        out_channels=16,
        text_dim=TEXT_DIM,
        freq_dim=256,
        ffn_dim=32,
        num_layers=2,
        cross_attn_norm=True,
        qk_norm="rms_norm_across_heads",
        image_dim=4,
        rope_max_seq_len=32,
        motion_encoder_channel_sizes={"4": 16, "8": 16, "16": 16},
        motion_encoder_size=TINY_FACE_SIZE,
        motion_style_dim=8,
        motion_dim=4,
        motion_encoder_dim=16,
        face_encoder_hidden_dim=16,
        face_encoder_num_heads=2,
        inject_face_latents_blocks=2,
    )


def _build_tiny_tokenizer() -> PreTrainedTokenizerFast:
    words = ["<pad>", "</s>", "<unk>", "a", "person", "dancing", "blurry", "sharp", "and", "bright"]
    backend = Tokenizer(models.WordLevel({w: i for i, w in enumerate(words)}, unk_token="<unk>"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    return PreTrainedTokenizerFast(
        tokenizer_object=backend, pad_token="<pad>", eos_token="</s>", unk_token="<unk>"
    )


def build_tiny_pipeline(device: str = "cpu") -> WanAnimatePipeline:
    torch.manual_seed(0)
    vae = AutoencoderKLWan(
        base_dim=3,
        z_dim=16,
        dim_mult=[1, 1, 1, 1],
        num_res_blocks=1,
        temperal_downsample=[False, True, True],
    )
    torch.manual_seed(0)
    # eval(): a directly constructed T5 stays in training mode, and dropout breaks determinism.
    text_encoder = T5EncoderModel(
        T5Config(vocab_size=32, d_model=TEXT_DIM, d_ff=37, d_kv=8, num_layers=2, num_heads=2)
    ).eval()
    torch.manual_seed(0)
    image_encoder = CLIPVisionModelWithProjection(
        CLIPVisionConfig(
            hidden_size=4,
            projection_dim=4,
            num_hidden_layers=2,
            num_attention_heads=2,
            image_size=4,
            intermediate_size=16,
            patch_size=1,
        )
    )
    pipe = WanAnimatePipeline(
        tokenizer=_build_tiny_tokenizer(),
        text_encoder=text_encoder,
        vae=vae,
        scheduler=FlowMatchEulerDiscreteScheduler(shift=7.0),
        image_processor=CLIPImageProcessor(crop_size=4, size=4),
        image_encoder=image_encoder,
        transformer=build_tiny_transformer(),
    )
    pipe.set_progress_bar_config(disable=True)
    return pipe.to(device)


def replacement_inputs(num_frames: int = 9, size: int = 16, seed: int = 0) -> dict[str, Any]:
    """Valid replacement-mode arguments with blank frames and a fixed CPU generator."""
    rgb = Image.new("RGB", (size, size))
    return {
        "image": rgb,
        "pose_video": [rgb] * num_frames,
        "face_video": [Image.new("RGB", (TINY_FACE_SIZE, TINY_FACE_SIZE))] * num_frames,
        "background_video": [rgb] * num_frames,
        "mask_video": [Image.new("L", (size, size))] * num_frames,
        "prompt": "a person dancing",
        "height": size,
        "width": size,
        "segment_frame_length": 9,
        "prev_segment_conditioning_frames": 1,
        "num_inference_steps": 2,
        "mode": "replace",
        "guidance_scale": 1.0,
        "generator": torch.Generator("cpu").manual_seed(seed),
        "output_type": "pt",
        "max_sequence_length": 16,
    }
