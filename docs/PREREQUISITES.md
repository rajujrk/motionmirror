# Prerequisites

## Every machine

Docker with Compose (OrbStack, Docker Desktop or Docker Engine), Python 3 for the scripts, `uv`, Node 26 with pnpm 10.14, a Java 21 JDK, and FFmpeg for tests and native workers.

```bash
make doctor     # Reports the machine, runtime, architecture, emulation, MPS, FFmpeg codecs and GPU. Changes nothing
```

## Platforms

| Platform | Status | Notes |
| --- | --- | --- |
| macOS on Apple Silicon | **Verified** on an M1 Pro with 16 GB | Full stack, real masks and poses through a native worker |
| Ubuntu, no GPU | Written, **unverified** | Should behave like the Mac without MPS |
| Windows with WSL2 | Written, **unverified** | See `docs/LOCAL_DEVELOPMENT.md` |
| Ubuntu with an NVIDIA GPU | Written, **`pending GPU validation`** | The only tier that can produce real output |
| Windows with an NVIDIA GPU | Written, **unverified** | GPU passthrough through WSL2 is a separate path to verify |

## Hardware tiers

| Tier | What it's for | Measured |
| --- | --- | --- |
| `cpu` | Application development, real masks slowly, smoke training | See `docs/BENCHMARKS.md` |
| `mps` | The same, a little faster for tracking | See `docs/BENCHMARKS.md` |
| `coreml` | Tested for pose, with no real gain | See `docs/BENCHMARKS.md` |
| `cuda` | Real generation | **Nothing measured yet** |

## GPU planning, not requirements

No GPU figure here is a measured requirement, because no GPU run has happened.

- 48 or 80 GB rental classes are planning candidates only. Real generation memory is unknown until `make benchmark-gpu` runs.
- Decoded frames stay on the CPU through a wrapper, so about 10 GB of GPU memory that the pinned pipeline would otherwise hold for a 900-frame clip at 720p is avoided. That figure is calculated, not measured.
- **Training is another matter.** The trainer's own script says `# 1*80G GPU cannot train Wan2.2-Animate-14B LoRA` and `# We tested on 8*80G GPUs`. Plan on that being expensive until a calibration says otherwise.

## Disk

| What | Size |
| --- | --- |
| Local images and volumes | Reported by `docker system df` |
| Preprocessing weights | About 2.7 GB |
| Real generation weights | About 83 GB |
| Training checkpoint | About 51 GB |
| TikTok dataset | 48.6 GB |
