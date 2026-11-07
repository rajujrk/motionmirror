# CI and releases

**Status: written, not yet run.** This repository has no remote, so no workflow has executed. `tests/tools/test_ci.py` checks them statically.

## Before pushing

```bash
make test               # unit and contract tests, no containers or weights
make test-integration   # real PostgreSQL, RabbitMQ, Redis and storage
make test-e2e           # browser journeys through the running stack (make up-mac first)
make test-failure-recovery
make test-security      # scanners, plus authorization and upload abuse
```

## Workflows

| File | Trigger | What it does |
| --- | --- | --- |
| `ci.yml` | Push to `main`, pull requests | Web, API, worker, training, tooling, browser journey and scans on Linux. Tooling also on Mac and Windows, and worker tests on Mac |
| `release.yml` | Tag `v*.*.*` | Builds images, pushes by digest, generates a CycloneDX SBOM, signs with Cosign |
| `gpu-acceptance.yml` | Manual only | The real GPU run. Needs a self-hosted GPU runner, and only starts when someone types `I authorize GPU spend` |

Hosted Mac and Windows runners have no Docker daemon, so service tests run on Linux only. Actions are pinned to full commits, and workflows default to read-only. Only the release job can write packages or sign.

## Releasing

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Then take the digests from the release run for `MM_API_IMAGE`, `MM_WEB_IMAGE`, `MM_WORKER_VALIDATE_IMAGE` and the Pod image. Verify the signature (`docs/SECURITY.md`) and the architecture before starting a Pod:

```bash
docker buildx imagetools inspect ghcr.io/<owner>/<repo>/worker-cuda@sha256:<digest>   # must show linux/amd64
```

To build the CUDA image without CI, on any machine: `make build-worker-amd64`.

## Rollback

Deploy the previous digests. Migrations are backward compatible and never run down automatically, so a rollback doesn't touch the database. Task envelopes carry a version, and unknown versions go to the dead-letter queue rather than being guessed at. To return a preset to its previous revision, `make adapter-rollback PRESET=<id>` needs no redeploy.

## Exceptions

A high or critical scanner finding needs a fix or a `.trivyignore` entry with an owner, a reason and an expiry date. There's no blanket bypass.
