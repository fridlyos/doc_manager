# Phase 4.e — Search UI & Vector/Catalog Consistency Check

**Status:** ✅ complete · **Branch:** `phase-4-search` · **Spec:** TECHSTACK §5.9, §14 (Phase 4.e)

The final Phase 4 step: an operator-facing Search screen over the `/search` API,
and a background job that verifies the SQL `chunks` rows agree with the Qdrant
vector points. Completing it closes Phase 4.

---

## 1. Consistency check (`catalog_consistency_check` job)

TECHSTACK 5.9 requires "consistency checks comparing SQL chunk records with vector
points." The handler (`jobs/handlers/consistency.py`) is now registered in
`HANDLERS` and diffs, for the **active embedding profile**:

- **Expected** point ids — derived deterministically from each `chunks` row:
  `point_id(content_object, chunking_profile, embedding_profile, chunk_index)`.
- **Actual** point ids — from Qdrant via `point_ids_for_content(content_id,
  embedding_profile_hash=…)` (now filtered by embedding profile and guarded when
  the collection does not exist yet).

`ConsistencyReport` accumulates per content object: chunks expected, points found,
**missing** points (row without a point), **orphan** points (point without a row),
and the set of drifted content objects. `report.clean` is true when there is no
drift. The result is emitted as a structured log and the job completes through the
normal lease-fenced `engine.complete`.

**Report-only by design.** It never mutates the store: repairing missing points is
`index_file`'s job (re-embed) and removing orphans is `remove_stale_vectors`. The
profile is resolved with `resolve_embedding_profile` (a FastEmbed registry lookup,
**no model load**), so the check is cheap.

A whole-collection orphan sweep (points for a content object with *no* rows at all)
is a deeper scan left to `remove_stale_vectors`; this check covers content objects
that still have rows, which is where indexing drift shows up.

## 2. Search UI

New `SearchPage` (`/search`, added to the primary nav):

- **Query box** plus optional **Location** dropdown (populated from
  `GET /locations`) and a free-text **Extensions** field (comma-separated, dots and
  case normalized client-side).
- Submits `POST /api/v1/search` via `client.ts::search`; filters are included only
  when set, so an unfiltered search sends just `{ query }`.
- Renders each hit as a card: primary **display_path** (`code`), a **page** chip
  (`p.4` / `pp.4–6`), an **availability** badge (current / missing / historical,
  colour-coded), the **similarity score**, and the **snippet**. Empty, loading, and
  error states are handled.

The UI shows only server-resolved `display_path`s and never constructs a query
from a filesystem path — consistent with the API's path-safety rules.

## 3. Verification

- **Backend — consistency (`test_consistency.py`, 4 tests):** `ConsistencyReport`
  accounting (missing + orphan); and against real PostgreSQL + an in-memory Qdrant,
  `scan_consistency` reports **clean** when points match, detects a **missing**
  point, and detects an **orphan** point.
- **Frontend — SearchPage (`SearchPage.test.tsx`, 2 tests):** submitting a query
  renders the hit's path, page label, availability, score, and snippet, and sends
  `{ query }`; the extensions field is normalized into
  `filters.extensions: ["pdf","md"]`.

Gate: full backend suite **163 pass**; ruff + mypy clean. Frontend **15 pass**;
eslint + tsc clean; production build succeeds.

## 4. Phase 4 is complete

All deliverables 4.a–4.e plus the `index_file` integration are done. Exit criteria:

- **No duplicate chunks/points on re-index** — deterministic ids + idempotent
  upsert (4.a/4.c/4.d).
- **A known query retrieves expected synthetic evidence** — golden-query test
  (4.d), and the Search UI surfaces it (4.e).
- **Search works without a generation provider** — `/search` never touches a
  provider (4.d).

## 5. Follow-ups (Phase 5+)

- Repair actions: wire `remove_stale_vectors` (orphan tombstoning) and a
  reindex path for missing points; optionally expose the consistency summary via an
  API/status surface and a trigger.
- Chunk-target vs. model token budget alignment (open decision #1).
- Generation / Ask (Phase 5) builds on this retrieval layer.
