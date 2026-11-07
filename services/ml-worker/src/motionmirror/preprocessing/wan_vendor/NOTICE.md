# Vendored from Wan2.2

Source: https://github.com/Wan-Video/Wan2.2 at commit `1ea34ff48f87168174e12956e200b1d908b1c5ff`,
`wan/modules/animate/preprocess/`. Copyright 2024-2025 The Alibaba Wan Team Authors. Apache License 2.0.

Changes:

- `pose2d.py`: removed the `Yolo` detector class (its weights are AGPL-3.0) and the `Pose2d` wrapper. MotionMirror derives person boxes from the SAM 2 mask of the dancer the user selected.
- Imports rewritten to this package path.

`pose2d_utils.py` and `human_visualization.py` are otherwise unchanged, so pose rendering matches what the model was trained on.
