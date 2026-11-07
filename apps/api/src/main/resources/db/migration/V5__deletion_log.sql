-- What was deleted, kept apart from the data itself. Rows are copied to object storage, because restoring a database
-- backup also rolls back this table, and without an outside record deleted data would come back. Holds ids only.
CREATE TABLE deletion_log (
    id          UUID PRIMARY KEY,
    kind        TEXT        NOT NULL CHECK (kind IN ('PROJECT', 'ASSET', 'RESULT')),
    target_id   UUID        NOT NULL,
    owner_id    UUID        NOT NULL,
    deleted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    exported_at TIMESTAMPTZ
);
CREATE INDEX deletion_log_unexported ON deletion_log (deleted_at) WHERE exported_at IS NULL;
