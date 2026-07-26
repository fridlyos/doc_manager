# Phase 6 Progress Status

**Branch:** `phase-6-reindex-duplicates` (cut from `main` @ `ac6a03d`, Phase 5 merged via PR #6).

Scope = TECHSTACK §14 "Phase 6: Scheduled Re-indexing and Duplicates".

Phase 6 turns the content-addressed catalog (Phases 3–4) into operator-controllable
re-indexing (manual + profile-driven rebuilds) and surfaces the duplicate/coverage
reports the hashes already make derivable. **No new extraction/embedding logic** —
this layer schedules, fans out, reconciles, and reports over existing jobs.

## Deliverables

| # | Deliverable | Status |
| --- | --- | --- |
| 6.a | Per-location schedules + manual file/location/all re-indexing | **✅ complete** |
| 6.b | Profile-driven full re-index jobs (`reindex_all_for_profile`) | ⬜ not started |
| 6.c | Exact-file and normalized-text duplicate reports | ⬜ not started |
| 6.d | Reuse canonical content/vectors across exact/structure-equivalent paths; report text-equivalent-different-pagination without sharing chunks; delete/stale cleanup | ⬜ not started |
| 6.e | Duplicate and coverage UI | ⬜ not started |

## Exit criteria (whole phase)

1. Add/change/move/delete/restore changes converge after a scan.
2. Duplicate groups show every active location/path.
3. Model/profile changes produce an explicit, controlled rebuild (no silent
   incompatible writes; no automatic partial mixing).

## Already in place (reused, not rebuilt)

- **Reconciler (Phase 3.a):** `reconcile.py` already produces add / changed /
  unchanged / metadata-only / **moved** / **restored** / **missing** transitions by
  content hash. Exit criterion 1 is largely satisfied for add/change/move/restore;
  Phase 6 adds the **delete → vector/chunk cleanup** half and validation tests.
- **Content addressing (Phases 3–4):** `content_objects` (unique on
  `structure_hash + extraction_profile_hash + normalization_version`; `text_hash`
  indexed), `file_versions` (`sha256`), `chunks`, and Qdrant points keyed on the
  content object. Duplicate detection is therefore a *query*, and content/vector
  **reuse across structure-equivalent paths already happens** in `index_file`
  (6.d's reuse invariant is implemented; 6 formalizes + tests it).
- **Profile isolation (Phase 4):** an embedding-profile change already routes to a
  new Qdrant collection and never mixes incompatible vectors — the safety half of
  the "controlled rebuild" (6.b) exit criterion.
- **Scheduler (Phase 2):** `jobs/scheduler.py` already enqueues due **location
  scans** from `SourceLocation.scan_interval_minutes` without duplicating an open
  scan. 6.a builds re-index scopes on top; scan scheduling itself is done.
- **Jobs:** `JobType` already declares `reindex_document`, `reindex_all_for_profile`,
  `build_duplicate_report`, and `remove_stale_vectors` — Phase 6 **wires the
  handlers** (currently unregistered). `index_file` is the per-file worker;
  `documents/{id}/reindex` (Phase 3.e) already enqueues it deduped.
- **Consistency check (Phase 4.e):** `catalog_consistency_check` + the Qdrant
  repository's `point_ids_for_content` / `delete_for_content` give `remove_stale_vectors`
  its primitives.

## Contract anchors (TECHSTACK §5.4, §8; docs/api/contracts.md)

- Catalog + duplicates API (§8): `GET /documents`, `GET /documents/{id}`,
  `POST /documents/{id}/reindex` (exist); **new:** `GET /duplicates`,
  `GET /duplicates/{id}`, `GET /coverage`.
- Duplicate service (§5.4): **exact duplicates** = active `file_versions` sharing a
  `sha256`; **text duplicates** = distinct `sha256` sharing a normalized `text_hash`;
  groups list **all active paths + source locations**; groups are derived from
  authoritative hashes and must be **safely rebuildable** even if materialized.
- Reuse rule (§5.4): artifacts/chunks/embeddings are reused only when the
  `structure_hash` **and** processing profiles match; text-equivalent files with
  **different pagination stay separate** for citation correctness.

---

## Completed work

### 6.a — Manual re-indexing (file / location / all) ✅ (2026-07-26)

Delivered `jobs/handlers/reindex.py` `handle_reindex_bulk` (registered for
`JobType.reindex_all_for_profile`): scope-driven fan-out that selects eligible
entries (sha256 set, state `indexed|failed|unsupported`; `location` scope filters
by location) and enqueues a deduped `index_file` per entry under the parent's
`root_job_id`, reports progress, completes lease-fenced. Endpoints
`POST /locations/{id}/reindex` (scope location) and `POST /system/reindex`
(scope all) — Idempotency-Key + 202 + `Location` + idempotent replay
(`maintenance.py` router). File-level reindex stays `index_file` (resolves open
decision #2: reuse, no separate handler). Re-index is idempotent — no duplicate
chunks/points. 7 tests (2 PG+Qdrant fan-out/idempotency, 5 endpoint); full backend
suite **251 pass, 1 skipped**; ruff/mypy clean. **Full report:
`docs/architecture/phase-6a-reindex.md`.**

Progresses exit criterion 1 (change convergence — re-index applies current profiles
idempotently). Scan scheduling itself was already delivered in Phase 2.

---

## Planned work

### 6.a — Per-location schedules + manual file/location/all re-index

- **Scan schedules** already run (scheduler + `scan_interval_minutes`); 6.a exposes
  them cleanly and adds the re-index scopes:
  - **file** — `POST /documents/{id}/reindex` (exists; Phase 3.e enqueues
    `index_file`). Keep as the per-file entry.
  - **location** — `POST /api/v1/locations/{id}/reindex`: fan out an `index_file`
    per currently-`indexed`/`failed`/`unsupported` entry in the location, deduped on
    the scanner's `index:{entry_id}` key. Idempotency-Key + 202, a parent job for
    progress.
  - **all** — `POST /api/v1/system/reindex`: fan out across all locations.
- **`reindex_document` handler**: register a distinct job type that forces
  re-extraction of one entry (vs. `index_file`'s change-verify). Decide whether to
  reuse `index_file` with a `force` flag or a thin wrapper — **open decision #2**.
- Fan-out uses a parent/child job lineage (existing `root_job_id`) so the UI can
  show aggregate progress; children coalesce on the dedupe key so repeated requests
  never pile up.

Tests: location/all reindex enqueues one deduped job per eligible entry; idempotent
replay; a re-index of unchanged content is a no-op upsert (no duplicate chunks/points).

### 6.b — Profile-driven full re-index (`reindex_all_for_profile`)

- **Handler** `reindex_all_for_profile`: given a target profile change
  (extraction / chunking / embedding), enqueue an `index_file` for every catalog
  entry so each is re-extracted/chunked/embedded under the **current** profiles,
  producing new `content_objects`/`chunks`/points; then enqueue
  `remove_stale_vectors` to retire points/chunks left under the old profile.
- **Controlled rebuild (exit criterion 3):** profile isolation (Phase 4) already
  prevents incompatible vectors in the active collection; this job makes the rebuild
  *explicit and complete* rather than lazy/partial. A new embedding profile writes a
  new collection; the old one is dropped only after the rebuild succeeds.
- **Trigger:** a manual maintenance endpoint (`POST /api/v1/system/reindex` with a
  `scope: profile` / reason) — profiles are config/secrets, so an operator initiates
  it; the API never auto-detects a config change mid-request. **Open decision #3.**

Tests (PG + Qdrant): a profile-hash change → rebuild produces content/points under
the new profile and removes the old; partial failure leaves the old profile intact
(no mixed state).

### 6.c — Exact-file and normalized-text duplicate reports

- **Model + migration 0005:** materialized `duplicate_groups`
  (`kind: exact | text`, `hash`, `member_count`, `built_at`) and `duplicate_members`
  (group → `catalog_entry_id`, denormalized display path/location/state). Derived
  from authoritative hashes, so **rebuildable** by the job.
- **Handler** `build_duplicate_report`:
  - **exact** groups — active `file_versions` (current per entry) sharing a `sha256`
    with ≥2 members;
  - **text** groups — current content objects sharing a `text_hash` across **distinct**
    `sha256`/structure (text-equivalent, incl. different pagination) with ≥2 members;
  - each member carries its current `display_path`, `source_location_id`, and state,
    so a group lists **every active location/path** (exit criterion 2).
- **API:** `GET /api/v1/duplicates` (paginated group list, filter by `kind`),
  `GET /api/v1/duplicates/{id}` (group + members), `GET /api/v1/coverage`
  (per-source-location coverage: counts by state + cross-location missing copies).
  Paths are resolved from PostgreSQL (never Qdrant), display-only, no `scan_root`.

Tests (PG): two identical files → one exact group with both paths; two files with the
same text but different bytes/pagination → one text group, and their chunks/points are
**not** shared; rebuild is idempotent; a moved file updates the member path.

### 6.d — Canonical reuse invariant + delete/stale cleanup

- **Reuse invariant (already implemented, now formalized + tested):** `index_file`
  reuses a `content_object` (and its chunks/points) only when `structure_hash` +
  extraction/normalization + chunking/embedding profiles match; exact and
  structure-equivalent paths therefore **share citation-bearing chunks**, while
  text-equivalent-different-pagination files get **separate** content objects and are
  reported (6.c) but never share chunks. Add explicit tests asserting both halves.
- **`remove_stale_vectors` handler:** delete Qdrant points + `chunks` rows (and the
  `content_object`) for any content object no longer referenced by an **active**
  `file_version` — i.e. every referencing entry is `missing`/deleted. This is the
  **delete/restore convergence** half of exit criterion 1: a deleted file's vectors
  are retired; a restore re-links via hash without re-extraction (reconciler already).
- Wire `remove_stale_vectors` as (a) a maintenance job the scheduler can enqueue and
  (b) the cleanup tail of `reindex_all_for_profile`.

Tests (PG + Qdrant): delete all copies of a content object → `remove_stale_vectors`
removes its points/chunks; a surviving copy keeps them; restore reconverges;
structure-equivalent reuse shares one content object, text-equivalent does not.

### 6.e — Duplicate and coverage UI

- **DuplicatesPage** (`/duplicates`): list groups (exact / text badge), member count;
  expand to every active `display_path` + location + state; link each member to its
  document. Filter by kind.
- **CoveragePage** (`/coverage`): per-location coverage (indexed / failed / missing /
  unsupported counts) and cross-location missing-copy summary.
- `client.ts` `fetchDuplicates()`, `fetchDuplicateGroup(id)`, `fetchCoverage()`; a
  "Rebuild report" action posts `build_duplicate_report`. Nav + routes; Vitest tests.

---

## Security posture (must hold)

- Duplicate/coverage responses expose only server-resolved `display_path`s (never
  `scan_root`); no route accepts a filesystem path.
- Re-index/rebuild endpoints are durable, Idempotency-Key'd jobs — no synchronous
  heavy work in the request.
- A profile rebuild never mixes incompatible vectors: a new embedding profile writes
  a new collection; the old is dropped only after success (fail closed, no partial).
- Deletes converge to removed vectors, but source files are never modified
  (read-only sources).

## Open decisions (resolve during 6.a / 6.c)

1. **Materialize vs compute duplicates.** Materialized `duplicate_groups` tables
   (migration 0005) rebuilt by `build_duplicate_report` (UI perf, spec-allowed) vs.
   on-the-fly SQL in `/duplicates`. Leaning materialized + rebuildable.
2. **`reindex_document` vs `index_file` + force.** A distinct handler that forces
   re-extraction, or reuse `index_file` with a `force`/reason payload flag. Leaning
   reuse `index_file` (already idempotent) with an explicit force path; register
   `reindex_document` as a thin alias for API/job-type clarity.
3. **Profile-rebuild trigger.** Manual maintenance endpoint (operator initiates,
   profiles are config/secrets) vs. auto-detect a config change. Leaning manual
   `POST /system/reindex` with an explicit scope/reason.
4. **Coverage definition.** Minimum: per-location state counts + cross-location
   missing-copy (a content object present in location A but with no active copy in
   location B). Confirm whether "missing copy" is global or pairwise between selected
   locations (Phase 7 sync planning is pairwise; keep 6 coverage per-location +
   global duplicates).
5. **Stale-vector trigger cadence.** `remove_stale_vectors` as a scheduled
   maintenance tick vs. enqueued by reconcile when it marks entries missing/deleted.
   Leaning: enqueued by reconcile on deletion + as the tail of a profile rebuild.

## New dependencies

- None. Uses existing PostgreSQL, Qdrant (`qdrant-client`), and the durable job
  engine.

## Ops notes

- A profile rebuild is heavy (re-embeds the corpus); it is an explicit operator
  action and runs as durable background jobs with progress. A new embedding model
  requires the model pulled/available before the rebuild.
- `remove_stale_vectors` and `build_duplicate_report` are safe to re-run; both are
  rebuildable from authoritative PostgreSQL state.
- Migration `0005` adds duplicate-report tables; upgrade/downgrade round-trip must be
  verified like prior migrations. Live dev DB rolls forward with `alembic upgrade head`.
