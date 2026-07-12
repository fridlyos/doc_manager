# Backup and restore

Phase 1 delivers the backup **maintenance profile** and the safety scaffolding.
The full application-aware backup coordinator (advisory maintenance lock,
`pg_dump`/`pg_dumpall`, native Qdrant snapshot, artifact capture, manifest +
checksums, retention, restore drill) is implemented in Phase 8. The scripts in
`scripts/` are honest skeletons: they refuse to produce a set that looks
restorable before that work exists.

## Authority and recovery model

Recovery does not depend on Qdrant (TECHSTACK 11.4):

1. **PostgreSQL** is authoritative for locations, catalog state, hashes, jobs,
   profiles, chunk metadata, and Qdrant point IDs.
2. **Source documents** are authoritative for original content.
3. **Extracted-text artifacts** are immutable derived data that speed re-chunk /
   re-embed.
4. **Qdrant** is a rebuildable semantic index. A missing or incompatible Qdrant
   snapshot does not block recovery — the collection rebuilds from PostgreSQL
   plus source documents or artifacts. A snapshot only reduces recovery time.

## Storage layout

- Live DB data: Docker named volumes `postgres_data`, `qdrant_data` (local SSD).
- `backup_staging` named volume: temporary snapshot/dump work, local, so a NAS
  interruption cannot corrupt a completed set.
- NAS `DocManager/backups/`:
  - `incoming/<backup-id>/` — incomplete; never restorable.
  - `completed/<backup-id>/` — immutable set with checksums and a completion
    marker written **last**.

The writable NAS backup path is mounted only into the `backup` maintenance
service.

## Running a backup (Phase 1 skeleton)

```bash
make backup
# = docker compose --profile maintenance run --rm backup /scripts/backup.sh
```

The skeleton probes the destination, stages a working directory, and stops
before writing a completion marker — no restorable set is produced yet.

## Restore / verify (Phase 1 skeleton)

```bash
docker compose --profile maintenance run --rm backup /scripts/restore.sh <backup-id>
docker compose --profile maintenance run --rm backup /scripts/verify-backup.sh <backup-id>
```

Both require a completed set with a completion marker and refuse to act
otherwise.
