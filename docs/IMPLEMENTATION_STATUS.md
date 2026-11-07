# Implementation status

Updated after every task. Tiers: **P0** core flow, **P1** hardening gate, **P2** deferred.

**GPU validation performed: none.** Real inference, benchmarks, real LoRA training and the MVP gate are `pending GPU validation`.
Ubuntu, Windows/WSL2 and CI are unverified until run.

## Current position

Milestone 0, Phase 0 spikes.

## Completed

| Task | Tier | Evidence | Commands run |
| --- | --- | --- | --- |
| T0.1 Repo baseline | P0 | Documents moved to `docs/product/`, ADRs 0001-0007 written | `git`, `brew install openjdk@21`, `open -a OrbStack` |
| T0.2 Diffusers contract spike | P0 | 38 contract tests pass on CPU and MPS. Two upstream crash limits pinned. `docs/MODEL_CAPABILITIES.md` | `uv run --extra wan --group dev pytest tests/contract` |
| T0.5 Training-path spike | P0 | Source read at pinned commit. Found and fixed a silent adapter no-op (`load_adapter`). `docs/TRAINING.md` | `uv run --extra wan --group dev pytest tests/contract/test_adapter_key_compat.py` (6 passed) |

## Blockers

| Blocker | Affects | Needed |
| --- | --- | --- |
| No NVIDIA GPU available | Real inference, benchmarks, real LoRA, MVP and training gates | A CUDA machine |
| No git remote, `gh` absent, GitHub connector failed | Running CI | A pushed repository |
