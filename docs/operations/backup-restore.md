# Backup and restore

Both sides are implemented. The application-aware backup coordinator (Phase 8.c)
produces a checksum-verified, atomically published set; the restore coordinator
(Phase 8 DoD) verifies a set and restores it into an **empty** PostgreSQL via
`scripts/restore.sh` → `python -m doc_manager.restore`, and a
`verify-consistency` command gates the rebuilt vector index. An automated restore
drill (`tests/integration/test_restore_drill.py`) proves the whole
seed→backup→wipe→restore→rebuild→consistency→known-query path end to end.

## Authority and recovery model (TECHSTACK §11.4)

Recovery does not depend on Qdrant:

1. **PostgreSQL** is authoritative for locations, catalog state, content hashes,
   jobs, embedding profiles, chunk metadata, and the Qdrant point IDs.
2. **Source documents** are authoritative for original content.
3. **Extracted-text artifacts** are immutable derived data that speed re-chunk /
   re-embed.
4. **Qdrant** is a rebuildable semantic index. A missing or incompatible Qdrant
   snapshot does not block recovery — the collection rebuilds from PostgreSQL
   plus the extracted-text artifacts (or the source documents). A snapshot only
   shortens recovery time.

The practical consequence: **restore PostgreSQL first, then either restore the
Qdrant snapshot or rebuild the index**. A good catalog with no vectors is fully
recoverable; vectors with no catalog are not.

## What a backup set contains (TECHSTACK §11.5)

`completed/<backup-id>/`:

- `postgres.dump` — `pg_dump --format=custom` of the catalog database.
- `globals.sql` — `pg_dumpall --globals-only` (roles, not the data).
- `qdrant/` — optional native Qdrant snapshot **plus** the collection → active
  embedding-profile mapping. Optional: its absence is a recorded warning, not a
  failure, because the index is rebuildable.
- extracted-text artifacts referenced by the dump, copied in and checksummed.
- `manifest.json` — machine-readable inventory: file entries with SHA-256, the
  Qdrant mapping, the artifact inventory, a **non-secret** configuration checksum
  (embedding model, collection, chunk sizes), and any capture warnings.
- `SHA256SUMS` — checksums over every file in the set.
- `COMPLETED` — the completion marker, written **last**. Its presence is the only
  signal that a set is restorable.

No secrets ever enter a set (§12): only the non-secret config snapshot is
recorded, never keys or connection strings.

## Storage layout

- Live DB data: Docker named volumes `postgres_data`, `qdrant_data` on local SSD.
- `backup_staging` named volume: local scratch where a set is built, checksummed,
  and verified before publication, so a NAS interruption cannot corrupt a
  completed set.
- NAS `DocManager/backups/` (`DOCMAN_NAS_BACKUPS_HOST_PATH`, mounted at `/backups`
  **only** in the `backup` service):
  - `completed/<backup-id>/` — immutable, checksummed, carries `COMPLETED`.
  - `completed/<backup-id>.partial/` — an interrupted publish; never restorable,
    never counted by retention.

The writable NAS backup path is mounted only into the `backup` maintenance
service — never into api/worker/postgres/qdrant (§12).

## Running a backup

```bash
make backup
# = docker compose --profile maintenance run --rm backup /scripts/backup.sh
```

The flow (owned by `python -m doc_manager.backup run`): build in staging →
checksum + manifest → **verify staged set** → `copytree` to
`completed/<id>.partial` → verify the copy → atomic `rename` to `completed/<id>`
→ write `COMPLETED` last → prune per the GFS retention policy. An interrupted run
leaves at most a `.partial` and never a restorable set. See
`docs/architecture/phase-8c-backup.md` for the full design.

Retention defaults (§11.6, tune in `.env`): **14 daily / 8 weekly / 12 monthly**
(`DOCMAN_BACKUP_RETENTION_{DAILY,WEEKLY,MONTHLY}`). Pruning only ever removes sets
that carry a `COMPLETED` marker.

## Verifying a set

```bash
docker compose --profile maintenance run --rm backup /scripts/verify-backup.sh <backup-id>
```

Requires the `COMPLETED` marker and recomputes the SHA-256 of every file in
`SHA256SUMS`. Exit 0 only when the whole set verifies. Run this before trusting a
set for restore and after any copy to the NAS.

## Restore procedure

> The backup maintenance image carries `pg_dump`/`pg_restore`/`psql`
> (postgresql-client-16) and reaches `postgres` and `qdrant` on the compose
> network. Run restore steps from that one-shot service. **Always restore into
> empty/fresh database volumes** — never over a live catalog.

**0. Verify the set first.** Do not restore a set that fails `verify-backup.sh`.

**1. Bring up empty PostgreSQL + Qdrant volumes.** For a drill or a clean rebuild:

```bash
docker compose down            # or `make nuke` to delete volumes (DESTROYS data)
docker compose up -d postgres qdrant
```

`make nuke` removes `postgres_data` and `qdrant_data`; fresh volumes re-init with
page checksums (`--data-checksums`). For an in-place disaster recovery onto a new
host, start only postgres/qdrant with empty named volumes.

**2. Restore PostgreSQL.** The one command — verifies the set (marker + checksums
+ manifest) fail-closed, then restores globals and the custom-format dump:

```bash
make restore BACKUP_ID=<backup-id>
# = docker compose --profile maintenance run --rm backup /scripts/restore.sh <backup-id>
```

Under the hood `restore.sh` execs `python -m doc_manager.restore run <id>`
(`psql -f globals.sql` then `pg_restore --clean --if-exists --no-owner`). The
equivalent by hand, if you need to drive `pg_restore` directly:

```bash
docker compose --profile maintenance run --rm backup bash -lc '
  set -euo pipefail
  SET=/backups/completed/<backup-id>
  # pg_restore/psql need a libpq URL, not the SQLAlchemy postgresql+psycopg://
  # form in DOCMAN_DATABASE_URL. Build one from the standard libpq env vars,
  # which Compose populates from .env (defaults shown).
  export PGHOST=postgres PGPORT=5432 \
         PGUSER="${DOCMAN_POSTGRES_USER:-docman}" \
         PGPASSWORD="${DOCMAN_POSTGRES_PASSWORD:-docman}" \
         PGDATABASE="${DOCMAN_POSTGRES_DB:-docman}"
  psql -f "$SET/globals.sql"
  pg_restore --clean --if-exists --no-owner --dbname "$PGDATABASE" "$SET/postgres.dump"
'
```

libpq reads `PGHOST`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` directly, so no URL is
needed. Set them from the same credentials the stack uses (compose defaults:
user `docman`, db `docman`).

**3. Rebuild the vector index.** The current coordinator does **not** store a
Qdrant snapshot in the set — it records only the collection/profile mapping in
the manifest (a downloadable snapshot is a future capability). So after a restore
the vector store is empty and is rebuilt from the catalog + the **live source
documents**. Bring the API and worker up (`docker compose up -d`), then:

```bash
curl -X POST 'http://127.0.0.1:8000/api/v1/system/reindex?rebuild_vectors=true'
```

- `reindex_all_for_profile` fans out one `index_file` per catalog entry. Each
  child **re-reads the live source file** (it re-stats and re-hashes
  `scan_root/relative_path` and skips a file that is missing or changed), re-runs
  extraction, and re-embeds + upserts vector points. The source mount must be
  present and unchanged — which it is, because sources are read-only and never
  part of a backup set.
- **`rebuild_vectors=true` is required after a same-profile restore.** The
  catalog's `chunks` rows survive the PostgreSQL restore, and the normal
  "already indexed" check is catalog-only — so a plain reindex would conclude the
  content is indexed and leave the empty Qdrant empty. The flag forces
  re-embedding. Upserts are idempotent on deterministic point ids, so it never
  duplicates. (Omit the flag only when the embedding profile changed, where the
  new profile already forces a re-embed.)
- This does not retire the previous profile's points; run
  `POST /system/remove-stale-vectors` separately after an embedding-profile change.

**4. Validate.** Gate on the SQL↔vector consistency check, then confirm a known
query:

```bash
make verify-consistency
# = docker compose --profile maintenance run --rm backup /scripts/verify-consistency.sh
```

`verify-consistency` scans the catalog's chunks against the Qdrant points for the
active embedding profile and exits non-zero if any point is missing or orphaned —
so a half-rebuilt index fails loudly. Then confirm `GET /api/v1/system/status` is
healthy and run a **known-query search** that should return specific evidence,
checking the expected documents + citations come back. The automated restore
drill (`tests/integration/test_restore_drill.py`) performs exactly this sequence.

## Known limitations of the restore path

- Point-in-time recovery between nightly dumps is not enabled by default; the
  nightly logical dump is the MVP recovery authority. See
  [`postgresql-pitr.md`](postgresql-pitr.md) for the optional WAL-archiving
  trade-off.
- The vector index is **not** stored in the set; it is always rebuilt from the
  catalog + live sources (with `rebuild_vectors=true`), so restore requires the
  source documents to be mounted and unchanged.
- Live Docker volume internals are never copied to the NAS; only the
  application-aware set crosses, so restore always goes through `pg_restore` +
  index rebuild, not a volume copy.

## Related

- [`postgresql-pitr.md`](postgresql-pitr.md) — optional PITR trade-offs.
- [`upgrade-migration.md`](upgrade-migration.md) — schema/image upgrades and when
  a full re-index is required.
- [`troubleshooting.md`](troubleshooting.md) — common backup/restore failures.
- `docs/architecture/phase-8c-backup.md` — backup coordinator design.
