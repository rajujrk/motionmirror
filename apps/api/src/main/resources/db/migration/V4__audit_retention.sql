-- Redacted, bounded metadata only: never prompts, tokens, signed URLs or face data.
CREATE TABLE audit_events (
    id          BIGSERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id    UUID,
    actor_type  TEXT        NOT NULL CHECK (actor_type IN ('USER', 'ADMIN', 'WORKER', 'SYSTEM')),
    action      TEXT        NOT NULL,
    target_type TEXT,
    target_id   TEXT,
    metadata    JSONB       NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_events_time ON audit_events (occurred_at DESC);

-- Kept after deletion so a restored backup can't resurrect deleted media.
CREATE TABLE deletion_tombstones (
    object_key  TEXT PRIMARY KEY,
    owner_id    UUID        NOT NULL,
    deleted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    purged_at   TIMESTAMPTZ
);
CREATE INDEX deletion_tombstones_pending ON deletion_tombstones (deleted_at) WHERE purged_at IS NULL;

ALTER TABLE assets ADD COLUMN consent_confirmed BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE generation_jobs ADD COLUMN consent_confirmed BOOLEAN NOT NULL DEFAULT false;
