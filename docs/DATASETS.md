# Datasets

`configs/datasets/registry.yaml` is the record. It holds only what a primary source or the downloaded files show. `commercial_use_ok` defaults to false.

| Dataset | Status | Why |
| --- | --- | --- |
| `tiktok-dance` | Approved, **research only** | CC BY-NC 4.0, and `License.pdf` limits it to research with no transfer to outside parties without permission. Real people who haven't individually consented. Reviewed by the product owner on 2026-11-06 |
| `ubc-fashion` | Blocked | Its official page states no licence |
| `humanvid-pexels` | Blocked | Terms not verified |
| Three Hugging Face dance sets | Rejected | No licence or source in their metadata |

A research-only dataset can only produce a labelled research adapter, enforced in the API. Production customization needs your own consented footage, with a written release, and uploads are never used by default.

## Commands

```bash
make datasets-fetch DATASET=tiktok-dance      # About 48.6 GB, resumable. Uses ~/.kaggle/kaggle.json
make datasets-validate DATASET=tiktok-dance   # Checksums the download against its manifest
make samples-build DATASET=tiktok-dance LIMIT=8
```

## Building samples

The archive is untrusted input. Paths are checked before use, frames must really be PNGs within a pixel limit, and nothing is written under a name the archive chose.

1. **Filter.** Every rejection is recorded with its reason: `no_mask`, `mask_count_mismatch`, `too_short`, `too_low_resolution`, `subject_missing_or_too_small`, `subject_fills_the_frame`, `near_duplicate_of_another_source`. 284 of the 340 clips are long enough.
2. **Split.** By a hash of the raw compilation, so a whole group lands together, and a leak fails the build. The dataset has no person ids, so the same person in two compilations could still leak. That limit is recorded in each dataset card.
3. **De-duplicate.** A difference hash of each clip's first frame. Nothing in the full dataset is within 6 bits across sources, so this drops nothing today.
4. **Preprocess.** The same pipeline serving uses, with the dataset's masks in place of SAM 2 and real ViTPose.
5. **Lay out for the trainer.** It takes its reference from frame 0 and pairs video frame `j` with control `j-4`. So `video.mp4` is the reference followed by 80 target frames, and control `k` shows target frame `k+3`. `test_samples.py` checks this exactly, using frames that carry their own index. The pairing is inferred from the trainer's code and needs checking on a real run.

Each build writes `manifest.json` (with the fixed evaluation set), `metadata_<split>.csv` for the trainer, and `DATASET_CARD.md`.

## Cut detection on this data

Used to refuse clips that cross a shot change. The threshold was chosen by measurement: 0.4 missed one of six real joins between clips, while 0.3 caught all six and flagged none of 26 clips (276 seconds). These are small samples, and heavy flash lighting wasn't tested. A false alarm only costs the user a trim, while a miss would track someone across a cut.

## Handling the data

Everything stays under the git-ignored `.data/`. If you copy it to a rented GPU, check the licence's transfer clause first. `make train-lora` asks you to confirm this.
