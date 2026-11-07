# ADR 0003: Selection-driven preprocessing built on SAM 2

**Status:** accepted, 2026-11-06.

**Evidence.** Wan's `preprocess/UserGuider.md` says its mask extraction is designed for single-person videos only and that
multi-person videos may give incorrect pose tracking. The product must replace one chosen dancer among several. The
preprocessing detector, YOLOv10 (THU-MIG), is AGPL-3.0. SAM 2 and ViTPose are Apache-2.0.

**Decision.** Seed SAM 2's video predictor with the user's points or box, derive each frame's person box from the mask, then
run ViTPose-H wholebody. Output the same `src_pose`, `src_face`, `src_bg` and `src_mask` as the official script. Avoid the
YOLOv10 weights. Use `decord`-free decoding.

**Consequences.** The same pipeline builds training data, so training and serving distributions match. The official script
remains the comparison baseline on single-person clips (T2.2). If the mask-derived box is inadequate, this is revisited.
