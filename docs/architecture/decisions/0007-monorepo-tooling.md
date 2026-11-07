# ADR 0007: Mono-repo tooling

**Status:** accepted, 2026-11-06.

**Decision.** A root `Makefile` is the single command surface. pnpm workspace for `apps/web` and `packages/contracts`, Maven
Wrapper for `apps/api`, uv for `services/ml-worker` and `training`, each with its own lockfile so training dependencies never
reach the inference image. No Turborepo or Nx: nothing here needs a task graph yet.

**Consequences.** Contracts in `packages/contracts` are the only cross-language coupling, tested by shared fixtures.
