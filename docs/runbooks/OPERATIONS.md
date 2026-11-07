# Operations

## When something fails

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Job stays "Waiting for a worker" | No worker for that queue | Start it, then `make worker-check`. The job runs once one appears |
| Job fails `CUDA_OOM` | Settings too large for the GPU | Lower the size or segment length. There's no silent quality drop, by design |
| Job fails `TARGET_LOST` | Tracker lost the dancer for most of the clip | Add marks where it goes wrong, or trim |
| Job fails `MODEL_MISSING` | Weights not installed | The message names the command |
| Leases keep expiring | Worker crashes or network loss | Worker logs, then `make worker-check` |
| Messages in `motionmirror.dead.*` | A delivery limit, or a malformed task | Inspect them before anything else. PostgreSQL is the authority, and the reconciler re-dispatches jobs, so most dead letters need no replay |
| Uploads stay quarantined | The validation worker or ClamAV is down | Scanner outages retry on purpose, so a file is never passed unscanned |

## Draining a worker

Send SIGTERM, which is what `docker stop` does. The worker takes no new work and gives the running attempt `MM_DRAIN_SECONDS` (default 60) to finish. Past that it stops the attempt, which reports a failure so the job retries at once rather than waiting out its lease. Compose allows 90 seconds for this. If the worker is killed outright, the attempt's process exits by itself within a few seconds, the lease expires, and another attempt runs. Outputs are fenced, so an old attempt can't interfere. `tests/e2e/recovery.spec.ts` exercises a kill, a freeze and an API restart.

## Backup

| What | How | Notes |
| --- | --- | --- |
| PostgreSQL | Managed snapshots, plus point-in-time recovery | Application and identity databases are separate. Backup retention decides how long deleted data can exist in backups, so state it in your privacy notice |
| Object storage | Versioning off for media, so a deletion really deletes | Keep `retention/deletions/` even if you lifecycle other prefixes |
| Redis | None needed | Rebuilds itself. Presets and capabilities come from PostgreSQL |
| RabbitMQ | Definitions only | Jobs recover from PostgreSQL through the reconciler |

## Restoring

**A restored database brings deleted data back,** along with forgetting it was deleted, because the tombstones are in the same database. That's why deletions are also recorded in object storage.

1. Keep the site closed. Restore PostgreSQL.
2. Start only the API, not the proxy.
3. `make reconcile-after-restore`, as an administrator. It applies every recorded deletion again, recreates tombstones, and audits the run. Running it twice is harmless.
4. Check the output: `recorded` is how many deletions are on record, and `reapplied` is how many the backup had undone.
5. Let the purger run, then open the site.

Deletions made in the last minute before a crash may not have been exported yet. `DeletionIT` tests this procedure.

## Retention and deletion

`MM_RETENTION_PROJECT_DAYS` deletes projects idle for that long, and never one with work still running. Uploads that were never finished go an hour after their form expires. See `docs/SECURITY.md`.

## Updating safely

Migrations are backward compatible, so the old version keeps working during a rolling update. Never run down-migrations automatically. Task envelopes are versioned, and an unknown version goes to the dead-letter queue. A preset file that changes an existing revision stops startup: give it a new revision.

After a restart: jobs resume from PostgreSQL, sessions survive, Redis rebuilds, and a worker with an adapter checks its cached hash before using it again.

## Teardown

Local: `make down` keeps data, and `make destroy-all-local-data` removes volumes. Cloud: see the runbook. Stopping a Pod doesn't stop all charges.
