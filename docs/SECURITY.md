# Security

What protects what, and the test that proves it. Scanner results are in `docs/IMPLEMENTATION_STATUS.md`.

## Threat model

| Threat | What stops it | Evidence |
| --- | --- | --- |
| Someone reads another person's clip, photo or result by guessing an id | Every read checks ownership. Other people's ids answer 404, so existence doesn't leak | `AccessControlIT` |
| A forged or stolen browser request changes data | CSRF tokens on every state change, `Secure` and `HttpOnly` session cookies, and a strict CSP with a per-request nonce | `AccessControlIT`, `tests/e2e/journey.spec.ts` |
| A malicious upload attacks the decoder or storage | Quarantine until bounded checks pass, then a copy to a server-only key. FFmpeg runs with argument arrays and `-protocol_whitelist file,pipe`, in a separate process that sees only local files | `UploadIT`, `test_validation.py`, `test_media.py` |
| A still-valid upload URL replaces a file after it was checked | The checked copy lives at a key the upload URL can't reach | `UploadIT` |
| Oversized uploads | Presigned POST with `content-length-range`, checked again on completion | `UploadIT` |
| Malware in an upload | ClamAV in the `security` profile and in production. It fails closed: when configured and unreachable, the file stays quarantined. A pass doesn't replace media checks | `test_clamav.py` |
| A compromised GPU worker reads everything | Workers hold short-lived tokens scoped to their backends. They get signed URLs only for their current attempt's inputs and outputs, and never touch the database or Redis | `LeaseIT`, `WorkerTokenValidationTest`, `make worker-check` |
| A stale worker overwrites a newer result | A fencing token on every call. A stale one gets `LEASE_LOST` | `LeaseIT`, `tests/e2e/recovery.spec.ts` |
| A malicious model file runs code | Adapters are safetensors only and hash-pinned, and admin-registered. Requests carry ids, never paths or URLs. The hash is checked on every use. Model repos load with no remote code | `AdapterLifecycleIT`, `test_runner.py` |
| Research data reaches production | A research-only adapter can be staged and never promoted, enforced in the API | `AdapterLifecycleIT` |
| Prompts or tokens leak through telemetry | Logs, spans and metric labels carry ids and outcomes only, and tests check for leaks | `ObservabilityIT`, `test_telemetry.py` |
| Deleted data returns after a restore | Deletions are recorded in object storage as well, and `make reconcile-after-restore` applies them again | `DeletionIT` |
| An insecure configuration goes live | Production refuses to start with mock mode, plain HTTP, insecure cookies, or plain connections to RabbitMQ or Redis | `StartupChecksTest` |

## Identity

- **Browser:** OAuth2 login with PKCE, with sessions in PostgreSQL through Spring Session JDBC.
- **Workers:** one service account per kind, with the token's issuer, audience, expiry and signature checked. `motionmirror-worker-wan` may run `wan-animate` and `real-preprocess`, `motionmirror-worker-validate` may run `media-validate`, and `motionmirror-worker-mock` may run `mock`. The production realm has no mock client and no test accounts (`scripts/render_realm.py`).
- **Operators:** the admin API takes a personal token from the device flow (`make adapter-login`) with the `ADMIN` role, so actions are audited to a real person. No password reaches the tool, and the token file is readable by its owner only.

## Secrets

`make bootstrap` generates every local secret into a git-ignored `.env` with owner-only permissions. Only salted RabbitMQ hashes are rendered to disk. The Redis ACL is rendered into tmpfs. Existing values are never changed when new settings are added. Production uses the deployment's secret store. Pod secrets on Runpod come from `infra/runpod/worker.env.template`, which holds placeholders only.

## Network

The public site serves the app, `/api` and identity, and refuses `/internal`, `/actuator` and `/auth/admin`. Workers get their own hostname that serves only the worker API and the token endpoint. `scripts/deploy_smoke.py` checks all of this against a live deployment. Everything published locally binds to loopback.

## Privacy and data lifecycle

- People confirm permission before uploading and before generating. Results are labelled AI-edited in the product and in the file's metadata.
- Uploads are never used for training.
- **Deleting** a project, clip, photo or result removes what was made from it, including output a failed attempt never reported. It also scrubs names from what remains, and stops running work at its next call.
- **Signed URLs** can't be recalled once issued, so tombstones stay open until they've expired, and the purger removes anything a late upload wrote. Fresh URLs stop at once. A deployment that needs instant revocation of downloads should proxy them.
- **Redis** holds presets and capabilities only, so no personal data needs purging from it.
- **Retention:** `MM_RETENTION_PROJECT_DAYS` deletes projects after that many idle days. 0 keeps them until their owner deletes them.
- **Biometric embeddings** aren't kept. ViTPose keypoints and SAM 2 masks exist only as the project's controls and are deleted with it.

## Supply chain

Compose images and CI actions are pinned to digests and commits (`tests/tools/test_ci.py`). Model revisions are pinned, and files are hash-checked against the hub's records. Releases carry an SBOM and a keyless Cosign signature. Verify one before deploying:

```bash
cosign verify ghcr.io/<owner>/<repo>/worker-cuda@sha256:<digest> \
  --certificate-identity-regexp 'https://github.com/<owner>/<repo>/.github/workflows/release.yml@refs/tags/v.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The Wan training checkpoint includes `.pth` pickles because the pinned trainer needs them. They stay in the training image, which is separate from serving, and only exported safetensors move on.

## Licence obligations you carry

The TikTok dataset's licence says the data "will not be transferred to outside parties without the authors' permission". Copying it to a rented GPU may count, so `make train-lora` refuses to start until you confirm you have that permission. Nothing in this repository sends dataset frames anywhere. Output from real clips was checked with numbers and with derived skeleton and mask images rather than raw frames, because viewing frames through an outside service could itself be a transfer. The contact sheets stay on disk for you to review.

## Scans

`make scan` runs Gitleaks, Semgrep and Trivy at pinned versions. A high or critical finding needs a fix, or an entry in `.trivyignore` with an owner, a reason and an expiry date.
