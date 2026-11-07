# ADR 0005: Platform support and what "without a GPU" means

**Status:** accepted, 2026-11-06.

**Evidence.** This workstation is an M1 Pro with 16 GB of memory. The Wan transformer is 34.55 GB, T5 11.36 GB and CLIP
4.77 GB. Linux containers on macOS cannot use the Apple GPU.

**Decision.**
- The full application runs on macOS, Ubuntu and Windows through WSL2 with Docker Desktop. `make` targets are not supported
  from native PowerShell.
- Without an NVIDIA GPU, selection and masks are real and generation is mock and clearly labelled, or delegated to a remote
  GPU worker. AMD and Intel GPUs count as CPU.
- Real inference requires CUDA. A native Mac backend is registered only if the T0.3 spike proves it, as `experimental`.

**Consequences.** Only macOS can be exercised from this workstation, so Ubuntu, Windows and CI stay unverified until run.
