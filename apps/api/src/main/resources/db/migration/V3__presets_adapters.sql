-- Preset revisions are immutable. preset_heads says which one is the default.
CREATE TABLE presets (
    id          TEXT        NOT NULL,
    revision    INT         NOT NULL,
    display_name TEXT       NOT NULL,
    backend_id  TEXT        NOT NULL,
    definition  JSONB       NOT NULL,
    adapter_id  UUID,
    visibility  TEXT        NOT NULL DEFAULT 'PUBLIC' CHECK (visibility IN ('PUBLIC', 'ADMIN')),
    created_by  UUID        REFERENCES users (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, revision)
);

CREATE TABLE preset_heads (
    id               TEXT PRIMARY KEY,
    current_revision INT         NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (id, current_revision) REFERENCES presets (id, revision)
);

CREATE TABLE model_adapters (
    id                UUID PRIMARY KEY,
    name              TEXT        NOT NULL,
    base_model_id     TEXT        NOT NULL,
    base_revision     TEXT        NOT NULL,
    artifact_key      TEXT        NOT NULL UNIQUE,
    sha256            TEXT        NOT NULL,
    target_modules    JSONB       NOT NULL,
    rank              INT         NOT NULL CHECK (rank > 0),
    scale             DOUBLE PRECISION NOT NULL,
    license           TEXT        NOT NULL,
    license_scope     TEXT        NOT NULL CHECK (license_scope IN ('research_only', 'production')),
    dataset_versions  JSONB       NOT NULL,
    trained_on        JSONB       NOT NULL,
    eval_manifest     JSONB,
    state             TEXT        NOT NULL CHECK (state IN ('REGISTERED', 'EVALUATED', 'STAGED', 'PROMOTED', 'DEPRECATED')),
    registered_by     UUID        NOT NULL REFERENCES users (id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE presets ADD CONSTRAINT presets_adapter FOREIGN KEY (adapter_id) REFERENCES model_adapters (id);

CREATE TABLE adapter_promotions (
    id                UUID PRIMARY KEY,
    preset_id         TEXT        NOT NULL,
    preset_revision   INT         NOT NULL,
    previous_revision INT,
    adapter_id        UUID        REFERENCES model_adapters (id),
    action            TEXT        NOT NULL CHECK (action IN ('STAGE', 'PROMOTE', 'ROLLBACK')),
    actor_id          UUID        NOT NULL REFERENCES users (id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
