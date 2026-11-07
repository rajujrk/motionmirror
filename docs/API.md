# API

The OpenAPI document is `packages/contracts/openapi.json`, and `packages/contracts/generated/api.d.ts` is the TypeScript client. Regenerate both from the running stack with `make contracts`. It's disabled in production. Errors have a stable `code`, a readable `detail`, and a `requestId`.

## Browser

Sign-in is OAuth2 with PKCE, and the session cookie is `HttpOnly`. State changes need the `X-XSRF-TOKEN` header, matching the `XSRF-TOKEN` cookie from `GET /api/v1/auth/csrf`.

```text
POST   /api/v1/projects                                {"name": "..."}
GET    /api/v1/projects/{id}
DELETE /api/v1/projects/{id}                           Removes everything in it
DELETE /api/v1/assets/{id}                             Removes the file and what was made from it
DELETE /api/v1/jobs/{id}                               Removes one generated result
POST   /api/v1/projects/{id}/subject-selections        Marks, with an optional trim
POST   /api/v1/projects/{id}/appearance-profiles       Photos, one primary
GET    /api/v1/projects/{id}/jobs
GET    /api/v1/jobs/{id}/outputs/{name}/download-url
GET    /api/v1/models/capabilities                     The label a job would get, and the retention policy
```

A selection is refused with `TRIM_REQUIRED` past the length limit, and `MULTIPLE_SHOTS` when it crosses a cut.

## Workers

Bearer tokens from the client-credentials flow. Only reachable on the worker hostname in production.

```text
POST /internal/v1/workers/register
POST /internal/v1/workers/storage-probe
POST /internal/v1/jobs/{id}/acquire              200 lease, or 409 with DUPLICATE, OBSOLETE or CANCELLED
POST /internal/v1/jobs/{id}/heartbeat            Fenced. Reports whether cancellation was requested
POST /internal/v1/jobs/{id}/progress             Fenced
POST /internal/v1/jobs/{id}/artifact-access      Fenced. Named inputs only, attempt-specific outputs
POST /internal/v1/jobs/{id}/complete             Fenced
POST /internal/v1/jobs/{id}/fail                 Fenced
POST /internal/v1/adapters/{id}/artifact-access  Fenced. Only the adapter this job names
```

A stale fencing token, or a job that was deleted, answers `409 LEASE_LOST`.

## Operators

A personal bearer token with the `ADMIN` role, from `make adapter-login`. See `docs/MODEL_DEPLOYMENT.md`.

```text
POST /api/v1/admin/adapters/upload-target
POST /api/v1/admin/adapters
GET  /api/v1/admin/adapters
POST /api/v1/admin/adapters/{id}/evaluation
POST /api/v1/admin/adapters/{id}/stage           {"presetId": "standard"}
POST /api/v1/admin/adapters/{id}/promote         {"presetId": "standard"}
POST /api/v1/admin/adapters/{id}/deprecate
POST /api/v1/admin/presets/{id}/rollback
POST /api/v1/admin/retention/reconcile-after-restore
```
