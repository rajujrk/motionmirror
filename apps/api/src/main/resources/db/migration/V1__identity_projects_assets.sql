-- Sessions live in PostgreSQL, not the evictable cache.
CREATE TABLE spring_session (
    primary_id            CHAR(36) NOT NULL PRIMARY KEY,
    session_id            CHAR(36) NOT NULL UNIQUE,
    creation_time         BIGINT   NOT NULL,
    last_access_time      BIGINT   NOT NULL,
    max_inactive_interval INT      NOT NULL,
    expiry_time           BIGINT   NOT NULL,
    principal_name        VARCHAR(100)
);
CREATE INDEX spring_session_expiry ON spring_session (expiry_time);
CREATE INDEX spring_session_principal ON spring_session (principal_name);
CREATE TABLE spring_session_attributes (
    session_primary_id CHAR(36)     NOT NULL REFERENCES spring_session (primary_id) ON DELETE CASCADE,
    attribute_name     VARCHAR(200) NOT NULL,
    attribute_bytes    BYTEA        NOT NULL,
    PRIMARY KEY (session_primary_id, attribute_name)
);

-- Identity is issuer plus subject, never the mutable email.
CREATE TABLE users (
    id           UUID PRIMARY KEY,
    issuer       TEXT        NOT NULL,
    subject      TEXT        NOT NULL,
    email        TEXT,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE projects (
    id         UUID PRIMARY KEY,
    owner_id   UUID        NOT NULL REFERENCES users (id),
    name       TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
    status     TEXT        NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'DELETED')),
    version    BIGINT      NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX projects_owner ON projects (owner_id, created_at DESC) WHERE status = 'ACTIVE';

-- role separates the driving video from target appearance references.
CREATE TABLE assets (
    id                UUID PRIMARY KEY,
    project_id        UUID        NOT NULL REFERENCES projects (id),
    owner_id          UUID        NOT NULL REFERENCES users (id),
    role              TEXT        NOT NULL CHECK (role IN ('DRIVING_VIDEO', 'TARGET_REFERENCE')),
    status            TEXT        NOT NULL CHECK (status IN ('PENDING_UPLOAD', 'QUARANTINED', 'READY', 'REJECTED', 'DELETED')),
    display_name      TEXT        NOT NULL,
    declared_type     TEXT        NOT NULL,
    declared_bytes    BIGINT      NOT NULL CHECK (declared_bytes > 0),
    upload_key        TEXT        NOT NULL UNIQUE,
    object_key        TEXT        UNIQUE,
    size_bytes        BIGINT,
    checksum_sha256   TEXT,
    detected_type     TEXT,
    media             JSONB,
    rejection_code    TEXT,
    rejection_detail  TEXT,
    upload_expires_at TIMESTAMPTZ NOT NULL,
    version           BIGINT      NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX assets_project ON assets (project_id, created_at DESC);
CREATE INDEX assets_abandoned ON assets (upload_expires_at) WHERE status = 'PENDING_UPLOAD';

CREATE TABLE appearance_profiles (
    id         UUID PRIMARY KEY,
    project_id UUID        NOT NULL REFERENCES projects (id),
    owner_id   UUID        NOT NULL REFERENCES users (id),
    name       TEXT        NOT NULL,
    version    BIGINT      NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX appearance_profiles_project ON appearance_profiles (project_id);

CREATE TABLE reference_images (
    id         UUID PRIMARY KEY,
    profile_id UUID        NOT NULL REFERENCES appearance_profiles (id) ON DELETE CASCADE,
    asset_id   UUID        NOT NULL REFERENCES assets (id),
    view_label TEXT        NOT NULL CHECK (view_label IN ('FULL_BODY', 'FRONT', 'BACK', 'SIDE', 'THREE_QUARTER', 'FACE')),
    is_primary BOOLEAN     NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile_id, asset_id)
);
CREATE UNIQUE INDEX reference_images_one_primary ON reference_images (profile_id) WHERE is_primary;

-- A correction is a new revision, so earlier results stay explainable.
CREATE TABLE subject_selections (
    id                 UUID PRIMARY KEY,
    project_id         UUID        NOT NULL REFERENCES projects (id),
    owner_id           UUID        NOT NULL REFERENCES users (id),
    source_asset_id    UUID        NOT NULL REFERENCES assets (id),
    revision           INT         NOT NULL,
    frame_index        INT         NOT NULL CHECK (frame_index >= 0),
    timestamp_num      BIGINT      NOT NULL,
    timestamp_den      BIGINT      NOT NULL CHECK (timestamp_den > 0),
    coordinate_mapping JSONB       NOT NULL,
    annotations        JSONB       NOT NULL,
    trim_start_seconds DOUBLE PRECISION,
    trim_end_seconds   DOUBLE PRECISION,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_asset_id, revision)
);
