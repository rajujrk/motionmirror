# Copyright 2024-2025 The Alibaba Wan Team Authors. All rights reserved.
import os
import cv2
from typing import Union, List

import numpy as np
import torch
import onnxruntime

from motionmirror.preprocessing.wan_vendor.pose2d_utils import (
    read_img,
    box_convert_simple,
    bbox_from_detector,
    crop,
    keypoints_from_heatmaps,
    load_pose_metas_from_kp2ds_seq
)


class SimpleOnnxInference(object):
    def __init__(self, checkpoint, device='cuda', reverse_input=False, **kwargs):
        if isinstance(device, str):
            device = torch.device(device)
        if device.type == 'cuda':
            device = '{}:{}'.format(device.type, device.index)
            providers = [("CUDAExecutionProvider", {"device_id": device[-1:] if device[-1] in [str(_i) for _i in range(10)] else "0"}), "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
        self.device = device
        if not os.path.exists(checkpoint):
            raise RuntimeError("{} is not existed!".format(checkpoint))
        
        if os.path.isdir(checkpoint):
            checkpoint = os.path.join(checkpoint, 'end2end.onnx')

        self.session = onnxruntime.InferenceSession(checkpoint,
                                                    providers=providers
                                                    )
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        self.input_resolution = self.session.get_inputs()[0].shape[2:] if not reverse_input else self.session.get_inputs()[0].shape[2:][::-1]
        self.input_resolution = np.array(self.input_resolution)
        

    def __call__(self, *args, **kwargs):
        return self.forward(*args, **kwargs)
    

    def get_output_names(self):
        output_names = []
        for node in self.session.get_outputs():
            output_names.append(node.name)
        return output_names


    def set_device(self, device):
        if isinstance(device, str):
            device = torch.device(device)
        if device.type == 'cuda':
            device = '{}:{}'.format(device.type, device.index)
            providers = [("CUDAExecutionProvider", {"device_id": device[-1:] if device[-1] in [str(_i) for _i in range(10)] else "0"}), "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
        self.session.set_providers(providers)
        self.device = device


class ViTPose(SimpleOnnxInference):
    def __init__(self, checkpoint, device='cuda', **kwargs):
        super(ViTPose, self).__init__(checkpoint, device=device)

    def forward(self, img, center, scale, **kwargs):
        heatmaps = self.session.run([], {self.session.get_inputs()[0].name: img})[0]
        points, prob = keypoints_from_heatmaps(heatmaps=heatmaps,
                                            center=center,
                                            scale=scale*200,
                                            unbiased=True, 
                                            use_udp=False)
        return np.concatenate([points, prob], axis=2)


    @staticmethod
    def preprocess(img, bbox=None, input_resolution=(256, 192), rescale=1.25, mask=None, **kwargs):
        if bbox is None or bbox[-1] <= 0 or (bbox[2] - bbox[0]) < 10 or (bbox[3] - bbox[1]) < 10:
            bbox = np.array([0, 0, img.shape[1], img.shape[0]])
        
        bbox_xywh = bbox
        if mask is not None:
            img = np.where(mask>128, img, mask)

        if isinstance(input_resolution, int):
            center, scale = bbox_from_detector(bbox_xywh, (input_resolution, input_resolution), rescale=rescale)
            img, new_shape, old_xy, new_xy = crop(img, center, scale, (input_resolution, input_resolution))
        else:
            center, scale = bbox_from_detector(bbox_xywh, input_resolution, rescale=rescale)
            img, new_shape, old_xy, new_xy = crop(img, center, scale, (input_resolution[0], input_resolution[1]))

        IMG_NORM_MEAN = np.array([0.485, 0.456, 0.406])
        IMG_NORM_STD = np.array([0.229, 0.224, 0.225])
        img_norm = (img / 255. - IMG_NORM_MEAN) / IMG_NORM_STD
        img_norm = img_norm.transpose(2, 0, 1).astype(np.float32)
        return img_norm, np.array(center), np.array(scale)


