# Implementation status

Updated 2026-11-06. Resume from this file and `docs/superpowers/plans/2026-11-06-motionmirror-master-plan.md`.
Tiers: **P0** core flow, **P1** hardening gate, **P2** deferred.

**GPU validation performed: none.** Real inference, benchmarks, real LoRA training and the MVP gate are `pending GPU validation`.
Ubuntu, Windows/WSL2 and CI have not been run. Work paused when the session's usage limit was reached.

**The application does not run end to end yet.** `make up-mac` fails because `apps/web/Dockerfile` doesn't exist.

## Done

| Task | Tier | Evidence |
| --- | --- | --- |
| T0.1 Repo baseline | P0 | Documents in `docs/product/`, ADRs 0001-0007 |
| T0.2 Diffusers contract spike | P0 | Tiny-model tests on CPU and MPS, two upstream crash limits pinned, `docs/MODEL_CAPABILITIES.md` |
| T0.5 Training-path spike | P0 | Source read at DiffSynth `08fdc0da`, silent adapter no-op found and fixed, `docs/TRAINING.md`. Nothing run at real scale |
| T1.3 API, schema, identity | P0 | Security chains and Flyway V1-V4. Real Keycloak login not yet exercised |
| T1.4 Projects and assets | P0 | `UploadIT` on SeaweedFS |
| T1.5 Jobs, outbox, topology | P0 | `OutboxIT`, `docs/MESSAGING.md` |
| T1.6 Leases and reconciler | P0 | `LeaseIT`. The reconciler has no dedicated test yet |
| T1.7 Redis and quotas | P0 | `RedisBehaviourIT`, `docs/REDIS.md` |
| T1.8 Worker and mock backend | P0 | Unit, contract and broker-deadline tests under `services/ml-worker/tests` |

Commands: `./mvnw test -Dtest='*IT,*Test'` in `apps/api` (92 tests passed), and
`uv run --extra wan --group dev pytest tests/unit tests/contract tests/integration` in `services/ml-worker`.

## Partly done

| Task | Done | Left |
| --- | --- | --- |
| T0.6 Dataset spike | TikTok zip downloaded (48.6 GB), `License.pdf` read, layout inspected, paths checked for traversal | Checksum manifest, extraction, sample building. Person-level splits are limited: the data has no person ids |
| T1.1 Mono-repo and Compose | Compose, bootstrap, doctor | Web image, measured RAM and disk per profile, most Makefile targets |
| T1.2 Contracts | Schemas and fixtures run by both languages, OpenAPI exported | AsyncAPI validation, drift check |
| T1.9 Web journey | Tokens, landing page, API client, hooks, upload helper, coordinate module | Projects page, all six workflow steps, Dockerfile, Playwright |
| T2.4 Backend | Settings validation in Java and Python, adapter loader, presets | `backends/wan_animate.py` is referenced by the registry but does not exist |

## Not started

T0.3 Apple Silicon spike, T0.4 CUDA CLI spike, T2.1-T2.3, T2.5, T3.1-T3.3, T4.1 sample pipeline, T4.2-T4.4, T5.1-T5.4.
The admin adapter endpoints don't exist, and deletion isn't implemented.

## Known issues

- `*IT` tests only run with an explicit `-Dtest`; `mvn test` alone skips them.
- `ruff check` still reports some over-long lines in `services/ml-worker`.
- `.env`, Compose volumes and `.data/` (about 49 GB) are local and git-ignored.

## Blockers

| Blocker | Affects | Needed |
| --- | --- | --- |
| No NVIDIA GPU available | Real inference, benchmarks, real LoRA, MVP and training gates | A CUDA machine |
| DiffSynth notes one 80 GB GPU can't train this LoRA | Real training cost | The owner's hardware and budget decision |
| TikTok licence: "will not be transferred to outside parties without the authors' permission" | Copying the data to a rented GPU | Ask the authors first |
| No git remote, `gh` absent, GitHub connector failed | Running CI | A pushed repository |
