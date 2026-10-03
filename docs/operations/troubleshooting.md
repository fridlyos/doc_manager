# Troubleshooting

Common failures and how to resolve them, grouped by symptom. Start with the
[runbook](runbook.md) for health endpoints and the storage rules; this page is
the "it's broken, now what" companion.

## First checks

```bash
make ps                                             # are all services up?
docker compose logs -f api worker                   # recent errors
curl -s http://127.0.0.1:8000/health/ready          # 200 = PG + Qdrant ready
curl -s http://127.0.0.1:8000/api/v1/system/status  # per-component report
make preflight                                      # mounts + backup dest
```

`health/ready` returns 503 whenever a **required** service (PostgreSQL or Qdrant)
is down; optional generation providers being down does not fail readiness.

## Startup and readiness

**`health/ready` stays 503.** A required dependency isn't up. Check
`system/status` for which one. PostgreSQL or Qdrant may still be in their
healthcheck start period, or Qdrant may have refused the storage (below).

**Qdrant refuses to start / fails its filesystem check.** Qdrant runs a startup
filesystem-compatibility check and it **must not be bypassed** in production. The
cause is almost always storage: Qdrant data was pointed at an SMB/mapped-drive
path. Fix the storage (keep `qdrant_data` on a local named volume on SSD) — do
not override the check. See the storage rules in the [runbook](runbook.md).

**`relation "…" does not exist` / schema errors on first use.** Migrations were
never applied — they do **not** run automatically at container start. Apply them:

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic current   # confirm the head revision
```

See [`upgrade-migration.md`](upgrade-migration.md).

## Sources, scanning, mapped drives

**A location shows `unavailable` and a scan refuses to run.** By design: the
mount preflight found the source root missing or its sentinel file
(`DOCMAN_NAS_MOUNT_SENTINEL`, default `.docman-source-id`) absent. The worker
marks the location `unavailable` rather than treating an empty directory as "all
files were deleted." This is a safety feature — reconnect the drive / restore the
sentinel, don't work around it. Run `make preflight` to see which check failed.

**Mapped NAS drive invisible to Compose after reboot.** Drive-letter mappings are
**per Windows logon**. The mapping must exist in the *same* logon context that
runs Docker Desktop. Re-run `make up` and `make preflight` after reconnecting. If
drive-letter binding proves unreliable, the documented fallback is a read-only
CIFS Docker volume with a least-privilege NAS account (never for live PG/Qdrant
data) — see the [runbook](runbook.md) mapped-drive section.

**Scan finds nothing / files missing.** Confirm the file types are supported
(PDF, TXT, MD for MVP) and that the source is mounted read-only at `/sources/nas`
inside the worker (`docker compose exec worker ls /sources/nas`).

## Models and generation

**First scan/search is very slow, then fine.** Expected: the embedding model
downloads on the first embed and is cached in-process. See the cache caveat in
[`model-setup.md`](model-setup.md) — a recreated worker re-downloads it.

**Embedding model download fails / worker offline.** The FastEmbed model fetch
needs network on first use and after any container recreate (no persistent cache
by default). Restore connectivity or add a cache volume (see
[`model-setup.md`](model-setup.md)).

**Ask returns search-only / no generated answer.** No generation provider is
ready. If using Ollama, confirm it is running natively on Windows and reachable:

```bash
docker compose exec api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').status)"
```

A `200` and a pulled `DOCMAN_OLLAMA_CHAT_MODEL` are both required. See
[`model-setup.md`](model-setup.md).

**Ask fails closed with an external-generation error.** External generation
requires both gates: the deployment override (flag + secret) **and** every
evidence-bearing source's `external_generation_policy` set to `allow`. If any
evidence comes from an external-denied source, the request fails closed on
purpose — it never silently drops evidence or switches providers. See
[`external-processing.md`](external-processing.md). To hard-disable, bring the
stack up without the external override.

## Backup and restore

**`backup.sh` fails: "backup destination not writable."** The NAS backup path
(`DOCMAN_NAS_BACKUPS_HOST_PATH`, mounted at `/backups`) is disconnected or
read-only. The script write-probes it before running so a disconnected NAS can't
produce a half-set. Reconnect the drive and re-run `make backup`.

**`verify-backup.sh` reports mismatches or missing files.** The set is corrupt or
was altered — do **not** restore it. Checksums are recomputed against
`SHA256SUMS`; any mismatch fails the set. Fall back to the previous good set.

**A set under `completed/` has a `.partial` suffix / no `COMPLETED` marker.** It
was interrupted mid-publish and is **not restorable** by design; retention
ignores it. Re-run the backup. Never hand-complete a `.partial` set.

**Restore: `pg_restore` connection or role errors.** `pg_restore`/`psql` need a
libpq connection, not the app's `postgresql+psycopg://` URL. Set
`PGHOST/PGUSER/PGPASSWORD/PGDATABASE` from the compose credentials as shown in
[`backup-restore.md`](backup-restore.md). Restore `globals.sql` before the custom
dump, and always restore into **empty** volumes.

**After restore, search returns nothing.** The catalog restored but the vector
index did not. Rebuild it: `POST /api/v1/system/reindex`, then validate with a
known-query search (the Qdrant index is rebuildable from the catalog —
[`backup-restore.md`](backup-restore.md)).

## Jobs and workers

**A job is stuck `running` after a worker crash.** The reaper reclaims expired
leases (`lease_expired` → retry/fail) and a healthy worker picks the job back up
(Phase 8.a). Confirm a worker is running (`make ps`) and watch `system/status`.

**Abandoned rows accumulate (interrupted scans, expired idempotency records).**
The worker's maintenance loop GCs safely-abandoned `scan_observations` and expired
`idempotency_records` on `DOCMAN_MAINTENANCE_INTERVAL_SECONDS`; it never touches
open work. If disabled, this is expected to grow — re-enable the maintenance
loop.

## Related

- [`runbook.md`](runbook.md) — start/stop, health, storage rules, mapped-drive
  acceptance.
- [`backup-restore.md`](backup-restore.md) · [`upgrade-migration.md`](upgrade-migration.md)
  · [`model-setup.md`](model-setup.md) · [`provider-configuration.md`](provider-configuration.md)
  · [`external-processing.md`](external-processing.md).
