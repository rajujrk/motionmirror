# ADR 0004: SeaweedFS replaces MinIO for local S3-compatible storage

**Status:** accepted, 2026-11-06, subject to the T1.4 test.

**Evidence.** Secondary sources report the MinIO community repository moved to maintenance mode in December 2025 and was
archived in February 2026, with community images stopped in October 2025. SeaweedFS (Apache-2.0, release 4.52 on
2026-11-02) enforces `content-length-range` in `weed/s3api/s3api_object_handlers_postpolicy.go`.

**Decision.** Use a pinned SeaweedFS image locally, under the brief's "verified equivalent" clause. Production uses any
private S3-compatible provider through the same interface.

**Consequences.** T1.4 proves POST policy, size limits and overwrite protection against SeaweedFS. If that fails, try the
`pgsty/minio` fork and record the result.
