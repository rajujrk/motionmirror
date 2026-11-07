# Model customization

There are three levels, each with its own cost and proof.

## 1. Presets (no training)

`configs/presets/*.yaml` hold validated settings. A revision is immutable, because running jobs point at it. Changing a file's settings without a new `revision` stops startup rather than being ignored. A new revision becomes the default on the next start, unless a promoted adapter holds that place.

`preview` is 432x768, `standard` is 720x1280, and both flip for landscape clips. Rules enforced in Java and Python alike: sizes divisible by 16, `segment_frame_length` of 4N+1 and at least twice `prev_segment_conditioning_frames`, and no negative prompt unless `guidance_scale` is above 1.

Worth testing on a GPU: the edit region is the dancer's whole bounding rectangle, as in the official script. It covered 53% of the frame against the dancer's 32% on a test clip, and anything inside it is regenerated, including another dancer. `BLOCK_COLUMNS` and `BLOCK_ROWS` in `preprocessing/masks.py` can tighten it, while a roomier region helps different clothing and hair, so the right value needs real output to judge.

## 2. Trained adapters (LoRA)

Operator-run fine-tuning of the pinned base model. See `docs/TRAINING.md` and `docs/MODEL_DEPLOYMENT.md`. Today's only reviewed dataset is research-only, so its adapters can be staged for evaluation and can never become a production default. Production customization needs consented footage.

## 3. Research extensions

Per-user identity training and true multi-view conditioning aren't built. They would need a verified training path, consented data, a compute budget, a separate evaluation and explicit labelling.

## Multiple reference photos

Stored and shown, but only the primary photo reaches the model, because `WanAnimatePipeline` takes one reference image. Manifests record which were consumed and which only stored, and the UI says the same. A multi-view roadmap would start with a measured comparison of picking the best photo per pose, before anything needing new model code.
