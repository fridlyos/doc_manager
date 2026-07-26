# Phase 6.a — Manual Re-indexing (file / location / all)

**Status:** ✅ complete · **Branch:** `phase-6-reindex-duplicates` · **Spec:** TECHSTACK §5.11, §14 (Phase 6.a)

Operator-triggered re-indexing at three scopes over the existing durable job
engine. Scan scheduling already runs (Phase 2 scheduler); this step adds the
re-index scopes and the fan-out handler that drives them.

---

## 1. Scopes

- **file** — `POST /api/v1/documents/{id}/reindex` (Phase 3.e; enqueues an
  `index_file` deduped on `index:{entry_id}`). Unchanged.
- **location** — `POST /api/v1/locations/{id}/reindex` — re-index every eligible
  document in one location.
- **all** — `POST /api/v1/system/reindex` — re-index every eligible document across
  all locations.

Location and all are Idempotency-Key'd, return `202` + `Location: /jobs/{id}` +
`Retry-After`, and enqueue a single durable **fan-out parent** job — no heavy work
in the request.

## 2. Fan-out handler (`jobs/handlers/reindex.py`)

`handle_reindex_bulk` is registered for `JobType.reindex_all_for_profile` and
covers the manual `location` / `all` scopes now (Phase 6.b adds the profile-rebuild
semantics + stale-vector cleanup tail). It:

- selects **eligible** entries — those with an observed `sha256` and state in
  `indexed | failed | unsupported` (a `missing` file has nothing to read;
  `discovered`/`queued` already have indexing pending from a scan);
- restricts to one location for the `location` scope;
- enqueues an `index_file` per entry, **deduped** on the scanner's
  `index:{entry_id}` key and sharing the parent's `root_job_id`, so a re-index
  coalesces with any in-flight indexing and repeated requests never pile up;
- records progress (`total = eligible count`) and completes under the normal
  lease-fenced `engine.complete`.

Re-indexing is **idempotent**: `index_file` re-verifies the fingerprint, reuses the
content object when structure + profiles match, and upserts vector points on
deterministic ids — so re-indexing unchanged content creates **no duplicate chunks
or points** (verified).

## 3. Design decisions

- **Reuse `index_file`, don't add a distinct per-file handler** (open decision #2):
  file-level re-index stays `index_file`; the fan-out enqueues the same worker.
  `reindex_document` remains an available job-type name but is not separately wired —
  `index_file` is already idempotent and is the single source of indexing truth.
- **One handler for location + all + (later) profile** — the `reindex_all_for_profile`
  handler is scope-driven via its payload; Phase 6.b extends it rather than adding a
  parallel handler.

## 4. Verification

- **Integration (PG + in-memory Qdrant), 2 tests:** a location reindex parent
  **fans out one queued `index_file` per eligible entry**, and draining the children
  leaves entries `indexed` with **no new chunks** (idempotent); the `all` scope
  covers every location.
- **Endpoint (PG, TestClient), 5 tests:** location reindex → `202` durable
  `reindex_all_for_profile` job with the location target + idempotent replay;
  missing Idempotency-Key → `400`; unknown location → `404`; system reindex → `202`
  (no target); missing key → `400`.

Gate: full backend suite **251 pass, 1 skipped**; ruff + mypy clean.

## 5. Follow-ups

- **6.b** extends this handler: a profile-change rebuild re-indexes under the new
  profile and enqueues `remove_stale_vectors` to retire the old profile's points.
- Aggregate progress for a fan-out (children carry `root_job_id`) can be surfaced in
  the UI (6.e) by counting child `index_file` jobs.
