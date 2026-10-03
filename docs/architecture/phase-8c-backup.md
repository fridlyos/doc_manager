# Phase 8.c — Coordinated Backup Maintenance Profile

**Status:** ✅ complete · **Branch:** `phase-8-hardening-release` · **Spec:** TECHSTACK §11.4–§11.6, §12, §14 (Phase 8.c)

An application-aware backup set — PostgreSQL dump + globals, an optional Qdrant
snapshot + collection/profile mapping, and a checksummed extracted-text artifact
inventory — produced with the mandated discipline: build in local **staging**,
checksum + manifest, **verify**, atomically publish to `completed/<id>`, write the
`COMPLETED` marker **last**, then prune per the GFS retention policy. Run one-shot
via the `maintenance` compose profile. Nothing here writes to a source root, and no
secrets ever enter a backup set (§12).

---

## 1. Package layout (`backend/src/doc_manager/backup/`)

| Module | Responsibility |
| --- | --- |
| `retention.py` | Pure GFS selection: `select_retained` / `select_pruned` over `(id, datetime)` pairs. No filesystem. |
| `checksums.py` | Streamed `sha256_file`, `write_sums` (`SHA256SUMS`), `verify_sums` → `VerifyResult(ok, checked, mismatches, missing)`. |
| `manifest.py` | `Manifest` dataclass + `FileEntry`/`ArtifactEntry`, `to_json`/`write`/`read_manifest`, non-secret `config_checksum`. |
| `runner.py` | Orchestration: `run_backup(...)`, `BackupSteps` Protocol, `BackupResult`, `BackupError`. Testable — external captures injected. |
| `default_steps.py` | Real captures: `pg_dump --format=custom`, `pg_dumpall --globals-only`, Qdrant snapshot + mapping, artifact inventory. Needs live services. |
| `__main__.py` | CLI: `run` and `verify <id>`. Reads `DOCMAN_STAGING_ROOT` (`/staging`), `DOCMAN_BACKUP_ROOT` (`/backups`). |

The split lets `run_backup` be unit-tested over fake `BackupSteps` without a live
PostgreSQL/Qdrant; `DefaultSteps` supplies the real captures in the container.

## 2. Backup flow (`run_backup`)

1. `backup_id = <UTC>%Y%m%dT%H%M%SZ`; create `staging_root/<id>` (fails if it exists).
2. **Capture** into staging: `dump_postgres` → `postgres.dump` (custom format),
   `dump_globals` → `globals.sql`, `snapshot_qdrant` → optional snapshot file +
   collection/profile mapping, `inventory_artifacts` → `ArtifactEntry` list + warnings.
3. **Manifest** (`manifest.json`): file entries with SHA-256, the Qdrant mapping,
   the artifact inventory, a **non-secret** configuration checksum, and warnings.
4. **Checksums**: `write_sums` over the dump, globals, manifest, and snapshot (if any).
5. **Verify staged set** — mismatch/missing ⇒ `BackupError`, staging wiped, no publish.
6. **Publish** (`_publish`): `copytree` staging → `completed/<id>.partial`, verify the
   copy, `rename(.partial → <id>)` (atomic on one filesystem), then write the
   `COMPLETED` marker **last**. An interrupted run leaves at most a `.partial` and
   never a `COMPLETED` set — so nothing incomplete is ever counted as restorable.
7. Staging is scratch and removed on both success and failure.
8. **Prune** (`_prune`): among sets that carry a `COMPLETED` marker, keep newest per
   GFS bucket (daily/weekly/monthly), delete the rest. `.partial` sets are ignored.

`BackupError` and any capture exception clean staging and re-raise (`except
BaseException`), so a Ctrl-C mid-run cannot leave a half-built set behind.

## 3. Retention (§11.6, GFS)

`select_retained` keeps the newest set for each of the most recent `daily` days,
`weekly` ISO-weeks, and `monthly` months; a set qualifying in several buckets is
kept once. Defaults from settings: **14 daily / 8 weekly / 12 monthly**
(`backup_retention_{daily,weekly,monthly}`). `select_pruned` is the oldest-first
complement. Pure, so the policy is unit-tested independently of the filesystem.

## 4. No secrets, no source writes (§12)

- The manifest records only a **non-secret** config snapshot (embedding model,
  collection, chunk sizes) and its checksum — never keys or connection secrets.
- The backup set is PG dump + globals + optional Qdrant snapshot + artifact copies +
  checksums — application data only. Live Docker volume internals never cross to NAS.
- `default_steps` reads the artifact store **read-only** and writes only into
  staging / the backup destination. No source document root is ever touched.

## 5. Qdrant snapshot is a convenience (§11.4)

The PostgreSQL dump is authoritative; the vector index is rebuildable from the
catalog + artifacts. `snapshot_qdrant` records the collection/profile mapping and
asks Qdrant to create a snapshot, but a failure (or a download the client/deployment
can't perform) is a **warning**, not a backup failure — the mapping still lets a
restore rebuild vectors. The manifest carries `qdrant_snapshot: null` in that case.

## 6. Container + scripts

- **`deploy/backup.Dockerfile`** — the worker image plus PGDG **postgresql-client-16**
  (`pg_dump` must be ≥ the server major). One-shot; default CMD runs a backup.
- **`compose.yaml` `backup` service** (`profiles: ["maintenance"]`) — mounts the
  `backup_staging` volume, the artifact store **read-only**, the NAS backup bind
  (writable, mounted into this service only), and `./scripts` read-only. No
  long-running entrypoint; pass a script to override the CMD.
- **`scripts/backup.sh`** — checks both roots exist, write-probes the NAS
  destination, then `exec python -m doc_manager.backup run`.
- **`scripts/verify-backup.sh <id>`** — `exec python -m doc_manager.backup verify <id>`
  (requires the `COMPLETED` marker; recomputes every checksum).

Usage:

```bash
docker compose --profile maintenance run --rm backup /scripts/backup.sh
docker compose --profile maintenance run --rm backup /scripts/verify-backup.sh <backup-id>
```

## 7. Tests (`backend/tests/unit/test_backup.py`, 13)

- **Retention:** newest-per-bucket (two same-day sets collapse), weekly/monthly
  buckets, `select_pruned` = oldest-first complement.
- **Checksums:** write/verify round-trip, **tamper detection** (mutated file ⇒
  mismatch), missing file, missing `SHA256SUMS`.
- **Orchestration** over fake `BackupSteps`: a complete verified set with the
  `COMPLETED` marker + populated manifest + cleaned staging + no `.partial`; snapshot
  omitted ⇒ `qdrant_snapshot: null`; an **interrupted run leaves no completed set**;
  a staged-verify failure raises `BackupError` and publishes nothing; retention
  prunes to the newest 3 of 10 daily sets.

`default_steps.py` (live PG/Qdrant) is intentionally not unit-tested; the fakes
cover the orchestration and discipline.

## 8. Gate

`ruff` clean, `ruff format --check` clean, `mypy` clean (102 source files), `pytest`
**301 passed, 1 skipped**.
