# Benchmarks

Measured results only. **No GPU measurement exists.** Anything not measured says so.

## Environment

Apple M1 Pro, 16 GB, macOS 27.2, torch 2.15.0, transformers 5.22.2, onnxruntime 1.31.1, FFmpeg through the system install. The stack was stopped for these runs, but other light work ran at the same time, so treat times as within about 10%. The test clip is one real dancer from the TikTok dataset (`00055`, 604x1080 at 30 fps).

**About the memory figures.** `peak RSS` is `ru_maxrss` for that process, each measurement in its own. macOS compresses and swaps under pressure, so it varies with what else is running and can understate real demand. The numbers show direction rather than limits.

## Real preprocessing, whole pipeline

| Clip | Size | Frames | Result | Time | Per frame | Peak RSS |
| --- | --- | --- | --- | --- | --- | --- |
| 27 s, **before chunking** | 720x1280 | 815 | **Stopped by the watchdog** at +6.5 GB of swap, nothing written | | | 4.39 GB when stopped |
| 27 s, chunks of 120 | 720x1280 | 815 | **Finished**, 0 lost frames, 0 jumps | 1,572 s (26 min) | 1.93 s | 3.68 GB |
| 4 s, before chunking | 432x768 | 120 | Finished | 252 s | 2.1 s | 3.0 GB |
| 3 s, through the whole system in a browser | 720x1280 | 90 | Finished, labelled experimental | Part of a 3.8 min journey | | |

On the 27 s clip, tracking took 897 s (1.10 s per frame), and pose plus encoding took 652 s (0.80 s per frame). Decoding took 1.4 s, which is why frames are decoded again rather than kept.

Scratch space for that clip's outputs was 26 MB.

**A 30-second clip on this Mac** would take roughly 29 minutes by extrapolation, and hasn't been measured.

## SAM 2.1 small, tracking only (432x768)

IoU is against the dataset's own masks, from one click.

| Device | Frames | Chunk | Per frame | Mean IoU | Min IoU | Last quarter | Peak RSS | GPU memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CPU (first run) | 60 | None | 1.21 s | 0.949 | 0.854 | | 2.25 GB | |
| MPS | 60 | None | 1.06 s | 0.949 | 0.854 | 0.950 | 1.84 GB | 2.29 GB |
| MPS | 240 | None | 1.09 s | 0.952 | 0.854 | 0.945 | 3.68 GB | 2.37 GB |
| MPS | 240 | 120 | 1.08 s | **0.954** | 0.854 | **0.952** | 4.49 GB | 2.32 GB |
| MPS | 240 | 60 | 1.11 s | 0.948 | 0.854 | 0.935 | 2.66 GB | 2.29 GB |

- MPS is real (`model_device` reported `mps:0`) but only about 12% faster than the CPU.
- No operator needed a CPU fallback: these ran with `PYTORCH_ENABLE_MPS_FALLBACK` unset, where an unsupported operator raises an error.
- Chunks of 120 lost nothing here, and 60 starts to drift. That's why 120 is the default. This is one clip.
- The 240-frame chunk-120 peak reads higher than unchunked, while the full 815-frame run at 720x1280 peaked lower. That's the pressure effect described above. The clear result is the 815-frame run finishing, which it couldn't do before.

## ViTPose-H (432x768, 60 frames)

| Provider | Per frame | Mean keypoint confidence | Peak RSS |
| --- | --- | --- | --- |
| CPU | 0.82 s | 0.817 | 2.69 GB |
| CoreML | 0.82 s | 0.817 | 2.54 GB |

CoreML gives no meaningful gain, and session creation takes longer, so pose stays on the CPU.

## Real output checks on the 120-frame clip

| Check | Result |
| --- | --- |
| Edit region covers the dancer | Mean 1.000, min 0.9987 of the dataset's person pixels |
| Edit region's share of the frame | Mean 0.534, against the dancer's 0.325, so anything else in that rectangle is regenerated |
| Skeleton pixels inside the dancer's box | Mean 0.998, min 0.844 |
| Background pixels inside the person | Mean brightness 0.0 |

## Pending

| Measurement | Status |
| --- | --- |
| Real generation: GPU memory, time, cold and warm start | `pending GPU validation` |
| Real training: step time, peak memory | `pending GPU validation` |
| Real generation on this Mac from Q2_K GGUF | See `docs/MAC_APPLE_SILICON.md` |
| Control-plane load (`make test-load`) | See `docs/IMPLEMENTATION_STATUS.md` |
| RAM and disk per Compose profile | See `docs/MAC_APPLE_SILICON.md` |
