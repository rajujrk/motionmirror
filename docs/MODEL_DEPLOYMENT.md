# Deploying a trained adapter

**Status: built and tested on tiny weights. No real adapter exists yet, so nothing here has run with one: `pending GPU validation`.**

```text
TRAINED -> REGISTERED -> EVALUATED -> STAGED -> PROMOTED, with DEPRECATED and rollback
```

## The path

```bash
make adapter-login                                    # Device flow, as an administrator
make eval-adapter ADAPTER=<run dir>                   # On a CUDA worker. Base against adapter on fixed clips and seeds
make eval-approve ADAPTER=<run dir> REVIEWER="Name" FAILED="hands"   # A person's checklist. Lists anything that failed
make adapter-register ADAPTER=<run dir> EVAL=<run dir>/evaluation/eval_manifest.json
make adapter-stage ADAPTER=<id> PRESET=standard       # A new revision only administrators can see
# Run a canary: a real job with presetId "standard@<revision>", and compare it with the baseline.
make adapter-promote ADAPTER=<id> PRESET=standard     # Becomes the default. Research adapters are refused
make adapter-rollback PRESET=standard                 # The previous revision is the default again. No redeploy
```

## What each step enforces

| Step | Rule | Refusal code |
| --- | --- | --- |
| Register | The base model and revision match what this deployment serves | `BASE_REVISION_MISMATCH` |
| Register | The API hashes the stored bytes itself and compares them with the run manifest | `HASH_MISMATCH`, `ARTIFACT_NOT_UPLOADED` |
| Register | The storage key is chosen by the API. Requests can't name a path or URL | |
| Evaluate | Only a CUDA evaluation counts, and it needs a person's pass | `EVALUATION_NOT_PRODUCTION_CLASS`, `EVALUATION_NOT_PASSED` |
| Stage | Must have an evaluation | `EVALUATION_MISSING` |
| Promote | Never for research-only data, and it must be staged first | `RESEARCH_ADAPTER`, `ADAPTER_NOT_STAGED` |
| Deprecate | Not while it's a preset's default | `ADAPTER_IN_USE` |
| Rollback | Needs an earlier revision | `NOTHING_TO_ROLL_BACK` |

A smoke adapter can't be registered at all: the CLI refuses, and the API would reject its base model anyway.

## How it reaches a worker

A job stores a snapshot of the adapter its preset names, so promoting or rolling back never changes a job already submitted. For a job that names one, the worker:

1. Asks the API for access. The API checks the worker's current lease and that this job names this adapter (`ADAPTER_NOT_IN_JOB`).
2. Uses its cached copy, or downloads to `adapter-cache/<sha256>.safetensors`.
3. Checks the hash on every use, including from the cache (`ADAPTER_INTEGRITY`).
4. Loads through `load_adapter`, which reads safetensors only and fails unless every layer in the file attached (`ADAPTER_INCOMPATIBLE`).

Workers report the hashes they hold when they register. Adapters load per attempt, because each attempt is its own process, so there's no resident adapter to go stale after a rollback. Every output manifest records the adapter's hash, scale, licence scope and attached layer count.

## Why the layer count ignores some layers

On the real model, Diffusers also attaches zero-weight LoRA to `attn2.add_k_proj` and `attn2.add_v_proj`, because its config has image cross-attention layers. They change nothing, so they aren't counted against the file. Counting them would refuse every real adapter. `test_adapter_key_compat.py` checks the tiny model has the same layers as the real architecture, built from its pinned config with empty weights, and that all 400 trainer layers exist on it.

## Failure cases

| What happened | What you see | What to do |
| --- | --- | --- |
| Hash mismatch at registration | `HASH_MISMATCH` | Upload again. If it repeats, the file changed since the run |
| Base revision mismatch | `BASE_REVISION_MISMATCH` | Train against the serving model, or change `MM_MODEL_REVISION` deliberately |
| Worker cache miss | Nothing visible | It downloads and verifies |
| Tampered cache or artifact | Job fails with `ADAPTER_INTEGRITY`, with no retry | Investigate the storage path |
| Out of memory with the adapter loaded | Job fails with `CUDA_OOM`, with no silent quality drop | Lower the settings, or try a bigger GPU |
| A bad adapter is already the default | Results look wrong | `make adapter-rollback PRESET=<id>` |

## Still to check on a real run

- Whether adapters trained against the original checkpoint behave the same on the Diffusers conversion.
- The real exported key set from the trainer. Layer names are verified against the real architecture, and shapes and effect aren't.
