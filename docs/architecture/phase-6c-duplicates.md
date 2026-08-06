# Phase 6.c — Duplicate & Coverage Reports

**Status:** ✅ complete · **Branch:** `phase-6-reindex-duplicates` · **Spec:** TECHSTACK §5.4, §8; §14 (Phase 6.c)

Exact-file and normalized-text duplicate reports plus per-location coverage,
derived from the authoritative hashes the catalog already stores. Materialized for
UI performance and safely rebuildable (exit criterion 2: groups show every active
location/path).

---

## 1. Model + migration 0005

Materialized, rebuildable report:

- **`duplicate_groups`** — `kind` (`exact` | `text`), `group_hash` (the sha256 or
  text_hash), `member_count`, `built_at`.
- **`duplicate_members`** — `group_id` (cascade), `catalog_entry_id` (cascade),
  and a **denormalized** `source_location_id` / `display_path` / `state` / `sha256`
  so a group lists every active location + path without a read-time join.

Migration `0005` create/drop verified with an upgrade → downgrade → re-upgrade
round-trip against real PostgreSQL; conftest truncation updated.

## 2. `build_duplicate_report` handler (`jobs/handlers/duplicates.py`)

Registered for `JobType.build_duplicate_report`. A **full truncate-and-rebuild**
over currently-`indexed` entries (each has a current file version + content
object):

- **exact** groups — entries sharing a `sha256` (byte-identical), ≥2 members;
- **text** groups — entries sharing a `text_hash` across **≥2 distinct** `sha256`
  (text-equivalent, incl. different bytes/pagination) — a pure byte-identical set
  is reported only as exact, not text.

Each member's `display_path` is server-resolved (`core.display.display_path`), so
no `scan_root` leaks. Rebuild is idempotent (replace, not append).

## 3. API

- **`GET /api/v1/duplicates`** — keyset-paginated group list; `filter[kind]`;
  sort by `member_count` / `built_at` (default `-member_count`).
- **`GET /api/v1/duplicates/{id}`** — group + all members.
- **`GET /api/v1/coverage`** — per-source-location catalog coverage: entry counts
  by state (`indexed`/`failed`/…) + total. (Cross-location missing-copy is Phase 7
  sync planning — open decision #4 resolved: per-location coverage here.)
- **`POST /api/v1/duplicates/rebuild`** — Idempotency-Key'd durable
  `build_duplicate_report` job.

## 4. Reuse note (feeds 6.d)

Because a content object is shared across structure-equivalent paths, exact and
structure-equivalent duplicates already share citation chunks/points; text-equivalent
files with **different pagination** get separate content objects and appear as a
`text` group **without** sharing chunks. 6.d adds explicit tests of that invariant
plus orphan cleanup.

## 5. Verification

- **Handler (PG + in-memory Qdrant), 2 tests:** a real corpus (identical pair +
  whitespace-only-different pair) produces exactly one **exact** group (shared
  sha256) and one **text** group (2 distinct sha256, shared text_hash), members
  carrying the right paths; rebuild is idempotent (replace).
- **Endpoints (PG, TestClient), 5 tests:** list + `kind` filter; group detail with
  member paths + 404; coverage counts by state; rebuild enqueues a durable job;
  missing Idempotency-Key → 400.

Gate: full backend suite **262 pass, 1 skipped**; ruff + mypy clean; migration 0005
round-trips.

## 6. Follow-ups

- **6.d** — formalize/test the reuse invariant (structure-equivalent share chunks;
  text-equivalent-different-pagination do not) and wire `remove_stale_vectors`
  orphan cleanup for deleted content (delete-convergence).
- **6.e** — Duplicates + Coverage UI over these endpoints.
