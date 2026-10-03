# Backup and restore

The application-aware backup coordinator is implemented (Phase 8.c): a single
`maintenance`-profile command produces a checksum-verified, atomically published
backup set. The **restore** side is documented here as an operator procedure you
can run today with the tools already in the maintenance image; the fully scripted
empty-volume restore drill (`scripts/restore.sh`, which is still a skeleton) and
the automated post-restore consistency gate land with the Phase 8 release
Definition-of-Done drill.

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

## Restore procedure (manual, operator-run)

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

**2. Restore PostgreSQL** (globals first, then the custom-format dump):

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

**3. Restore or rebuild the vector index.** Bring the API and worker up
(`docker compose up -d`), then:

- **If the set has a Qdrant snapshot** and the manifest's embedding profile
  matches the current config: restore the snapshot into Qdrant (the
  collection/profile mapping is recorded under `qdrant/`). This is the fast path.
- **Otherwise (no snapshot, or a profile mismatch): rebuild from the catalog**:

  ```bash
  curl -X POST http://127.0.0.1:8000/api/v1/system/reindex
  ```

  `reindex_all_for_profile` re-embeds and re-upserts every chunk from the catalog
  + extracted-text artifacts, then retires stale vectors. This is always correct
  because the catalog is authoritative; the snapshot is only an optimization.

**4. Validate.** Confirm `GET /api/v1/system/status` is healthy, then run a
**known-query search** that you know should return specific evidence and confirm
the expected documents and citations come back. This is the consistency check an
operator can perform today; the automated `catalog_consistency_check` gate is
wired into the scripted restore drill delivered with the release DoD.

## Known limitations of the restore path (today)

- `scripts/restore.sh` is still a skeleton — it refuses to act and prints the
  intended steps. Use the manual procedure above until the DoD restore drill
  lands.
- Point-in-time recovery between nightly dumps is not enabled by default; the
  nightly logical dump is the MVP recovery authority. See
  [`postgresql-pitr.md`](postgresql-pitr.md) for the optional WAL-archiving
  trade-off.
- Live Docker volume internals are never copied to the NAS; only the
  application-aware set crosses, so restore always goes through `pg_restore` +
  index rebuild, not a volume copy.

## Related

- [`postgresql-pitr.md`](postgresql-pitr.md) — optional PITR trade-offs.
- [`upgrade-migration.md`](upgrade-migration.md) — schema/image upgrades and when
  a full re-index is required.
- [`troubleshooting.md`](troubleshooting.md) — common backup/restore failures.
- `docs/architecture/phase-8c-backup.md` — backup coordinator design.
