CREATE TABLE generation_jobs (
    id                  UUID PRIMARY KEY,
    project_id          UUID        NOT NULL REFERENCES projects (id),
    owner_id            UUID        NOT NULL REFERENCES users (id),
    kind                TEXT        NOT NULL CHECK (kind IN ('VALIDATE', 'PREPROCESS', 'PREVIEW', 'FULL')),
    state               TEXT        NOT NULL CHECK (state IN ('QUEUED', 'RUNNING', 'RETRY_WAIT', 'CANCEL_REQUESTED', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    stage               TEXT,
    backend_id          TEXT        NOT NULL,
    routing_key         TEXT        NOT NULL,
    config_revision     TEXT        NOT NULL,
    requested_settings  JSONB       NOT NULL DEFAULT '{}',
    effective_settings  JSONB       NOT NULL DEFAULT '{}',
    inputs              JSONB       NOT NULL DEFAULT '{}',
    idempotency_key     TEXT,
    dispatch_generation INT         NOT NULL DEFAULT 1,
    fencing_sequence    BIGINT      NOT NULL DEFAULT 0,
    attempt_count       INT         NOT NULL DEFAULT 0,
    max_attempts        INT         NOT NULL,
    next_attempt_at     TIMESTAMPTZ,
    failure_code        TEXT,
    failure_detail      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ
);
CREATE UNIQUE INDEX generation_jobs_idempotency ON generation_jobs (owner_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX generation_jobs_project ON generation_jobs (project_id, created_at DESC);
CREATE INDEX generation_jobs_owner_active ON generation_jobs (owner_id) WHERE state IN ('QUEUED', 'RUNNING', 'RETRY_WAIT', 'CANCEL_REQUESTED');
CREATE INDEX generation_jobs_due_retry ON generation_jobs (next_attempt_at) WHERE state = 'RETRY_WAIT';

CREATE TABLE job_attempts (
    id                  UUID PRIMARY KEY,
    job_id              UUID        NOT NULL REFERENCES generation_jobs (id) ON DELETE CASCADE,
    attempt_no          INT         NOT NULL,
    worker_id           TEXT        NOT NULL,
    fencing_token       BIGINT      NOT NULL,
    dispatch_generation INT         NOT NULL,
    event_id            UUID        NOT NULL,
    state               TEXT        NOT NULL CHECK (state IN ('ACTIVE', 'SUCCEEDED', 'FAILED', 'EXPIRED', 'CANCELLED')),
    stage               TEXT,
    progress            JSONB       NOT NULL DEFAULT '{}',
    lease_expires_at    TIMESTAMPTZ NOT NULL,
    last_heartbeat_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    failure_code        TEXT,
    failure_detail      TEXT,
    UNIQUE (job_id, attempt_no),
    UNIQUE (job_id, fencing_token)
);
CREATE UNIQUE INDEX job_attempts_one_active ON job_attempts (job_id) WHERE state = 'ACTIVE';
CREATE INDEX job_attempts_expiring ON job_attempts (lease_expires_at) WHERE state = 'ACTIVE';

CREATE TABLE job_events (
    id          BIGSERIAL PRIMARY KEY,
    job_id      UUID        NOT NULL REFERENCES generation_jobs (id) ON DELETE CASCADE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    type        TEXT        NOT NULL,
    detail      JSONB       NOT NULL DEFAULT '{}'
);
CREATE INDEX job_events_job ON job_events (job_id, id);

-- UNIQUE (job_id, name): a stale or duplicate attempt can never attach a second output.
CREATE TABLE outputs (
    id           UUID PRIMARY KEY,
    job_id       UUID        NOT NULL REFERENCES generation_jobs (id) ON DELETE CASCADE,
    attempt_id   UUID        NOT NULL REFERENCES job_attempts (id),
    name         TEXT        NOT NULL,
    object_key   TEXT        NOT NULL,
    sha256       TEXT        NOT NULL,
    size_bytes   BIGINT      NOT NULL,
    content_type TEXT        NOT NULL,
    media        JSONB       NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, name)
);

CREATE TABLE outbox_events (
    id                  UUID PRIMARY KEY,
    schema_version      INT         NOT NULL,
    job_id              UUID        NOT NULL REFERENCES generation_jobs (id) ON DELETE CASCADE,
    dispatch_generation INT         NOT NULL,
    routing_key         TEXT        NOT NULL,
    payload             JSONB       NOT NULL,
    state               TEXT        NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'RESERVED', 'PUBLISHED')),
    reserved_until      TIMESTAMPTZ,
    publish_attempts    INT         NOT NULL DEFAULT 0,
    next_attempt_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at        TIMESTAMPTZ
);
CREATE INDEX outbox_events_pending ON outbox_events (next_attempt_at) WHERE state <> 'PUBLISHED';
CREATE INDEX outbox_events_job ON outbox_events (job_id, dispatch_generation);

-- Durable record of what happened to each delivery. It explains recovery and never blocks a retry.
CREATE TABLE worker_deliveries (
    event_id     UUID        NOT NULL,
    job_id       UUID        NOT NULL REFERENCES generation_jobs (id) ON DELETE CASCADE,
    worker_id    TEXT        NOT NULL,
    disposition  TEXT        NOT NULL,
    deliveries   INT         NOT NULL DEFAULT 1,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, worker_id)
);

CREATE TABLE workers (
    worker_id    TEXT PRIMARY KEY,
    client_id    TEXT        NOT NULL,
    backends     JSONB       NOT NULL,
    device_type  TEXT        NOT NULL CHECK (device_type IN ('cuda', 'mps', 'cpu')),
    capabilities JSONB       NOT NULL DEFAULT '{}',
    adapters     JSONB       NOT NULL DEFAULT '[]',
    runtime      JSONB       NOT NULL DEFAULT '{}',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE preprocessing_artifacts (
    id              UUID PRIMARY KEY,
    project_id      UUID        NOT NULL REFERENCES projects (id),
    job_id          UUID        NOT NULL REFERENCES generation_jobs (id),
    cache_key       TEXT        NOT NULL UNIQUE,
    source_asset_id UUID        NOT NULL REFERENCES assets (id),
    selection_id    UUID        NOT NULL REFERENCES subject_selections (id),
    config          JSONB       NOT NULL,
    tool_revision   TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
