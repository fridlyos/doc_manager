# Phase 7 Progress Status

**Branch:** `phase-7-sync-planning` (cut from `main` @ `1c3696b`, Phase 6 merged).

Scope = TECHSTACK §14 "Phase 7: Multi-location Comparison and Sync Planning".

Phase 7 compares selected source locations and produces **persisted, read-only
dry-run sync plans** — matching / missing / renamed / conflicting content — and
**never writes to source roots**. Comparison runs over catalog hashes already in
PostgreSQL; no filesystem mutation exists anywhere in this feature.

## Deliverables

| # | Deliverable | Status |
| --- | --- | --- |
| 7.a | Pairwise location coverage reports | **✅ complete** |
| 7.b | Relative-path / hash / text comparison rules | **✅ complete** |
| 7.c | Persist + display dry-run sync plans and conflicts | **✅ complete** |
| 7.d | Document how a future separately-reviewed executor could consume a plan | **✅ complete** |

**Phase 7 is complete.** All deliverables and exit criteria are met.

## Exit criteria (whole phase)

1. Users can identify **matching, missing, renamed, and conflicting** content.
2. Integration + E2E tests prove the feature **never writes to source roots**.

## Already in place (reused, not rebuilt)

- **Catalog hashes:** `catalog_entries` (relative_path, state) → current
  `file_versions` (`sha256`) → `content_objects` (`text_hash`). Comparison is a pure
  SQL read over these — **no file access**, so "never writes to source" is
  structural, not merely tested.
- **Coverage (6.c):** per-location state counts + `GET /coverage`. 7.a adds the
  *pairwise* comparison on top.
- **Durable job engine + Idempotency-Key** (Phases 1–2): `build_sync_plan` is
  already a declared `JobType` (handler unregistered); `POST /sync-plans` is on the
  contract's idempotent job-creating list (§6.1).
- **Envelope / cursor pagination / Problem / display_path** plumbing, and the
  `duplicates`/`coverage` route pattern to mirror.
- **Read-only sources invariant** (whole system): the worker never mutates source
  roots; sync planning inherits it and adds no write path.

## Contract anchors (TECHSTACK §5.14, §6; docs/api/contracts.md)

- API (§8): `POST /api/v1/sync-plans`, `GET /api/v1/sync-plans/{id}`,
  `GET /api/v1/sync-plans/{id}/items`. **No execution endpoint in the MVP.**
- Plans are **immutable** once generated; generation uses atomic domain-state
  checks + Idempotency-Key (§6.1, §7).
- Planner (§5.14): compare by **relative path, file hash, and normalized text
  hash**; report exact matches, renamed equivalents, missing copies, conflicts;
  produce a persisted dry-run with proposed source/target paths; **never execute**.
- Data model (§6): `sync_plans` (compared source/target, status) + `sync_plan_items`
  (action `copy | conflict | already_present | manual_review`, source/target
  relative paths, hashes, reason, timestamps; **no execution columns**).

---

## Completed work

### 7.a / 7.b — Location comparison + pairwise coverage ✅ (2026-08-06)

Delivered pure `doc_manager/sync/compare.py`: `compare_locations(source, target)`
→ `ComparisonResult` (classified `SyncItem`s + `CoverageSummary`). Rules with
precedence path→hash→text→missing: `already_present` (exact), `conflict`
(same path, diff sha256), `manual_review` (`renamed` = same sha256 other path;
`text_equivalent` = same text_hash, diff bytes), `copy` (missing). Deterministic
target pick (lowest relative path); coverage counts + `covered_percent`
(0.0 on empty source). Directional (source authoritative). No DB/FS/execution.
11 unit tests; full backend suite **276 pass, 1 skipped**; ruff/mypy clean.
**Full report: `docs/architecture/phase-7a-comparison.md`.**

Coverage (7.a) and the comparison rules (7.b) are the same tested library; 7.c
feeds it catalog rows and persists the result.

### 7.c — Persisted dry-run sync plans ✅ (2026-08-06)

Delivered `sync_plans`/`sync_plan_items` (migration 0006, round-trip verified;
no execution columns), `build_sync_plan` handler (loads indexed snapshots → runs
`compare_locations` → persists items + coverage summary → `ready`; catalog hashes
only, opens no file), API `POST /sync-plans` (Idempotency-Key durable 202 +
replay; 422 same source/target; 404 unknown) + `GET /sync-plans[/{id}[/items]]`
(paginated, `filter[action]`), **no execute route**, and `SyncPlansPage` UI
(create + list + expand items with conflict/copy highlight). Resolves open
decision #1 (202 durable). Backend **285 pass, 1 skipped** (9 new incl. the
no-write source-root E2E); frontend **24 pass** (2 new); migration 0006
round-trips; ruff/mypy/eslint/tsc clean; build succeeds. **Full report:
`docs/architecture/phase-7c-sync-plans.md`.**

Completes exit criterion 1 (identify matching/missing/renamed/conflicting) and
criterion 2 (E2E proves no writes to source roots). Only 7.d (executor ADR) remains.

### 7.d — Future-executor boundary ADR ✅ (2026-08-06)

Delivered `docs/adr/0006-sync-executor-boundary.md` (indexed in `docs/adr/README.md`):
records that **no sync execution ships in the MVP** — planning is comparison +
immutable dry-run plans only — and pins the properties any future, separately
reviewed executor MUST satisfy (disabled by default; allowlisted target roots only;
explicit per-operation confirmation; conflict rules, never auto-overwrite; no
automatic delete; checksum-after-copy; audit trail; execution-time re-validation;
its own security review). Notes the MVP's structural guarantees (build opens no
file; no execute route; no execution columns). Docs-only.

**Phase 7 complete** — all deliverables 7.a–7.d; both exit criteria met.

---

## Planned work

### 7.a — Pairwise coverage + comparison data (pure `sync/` library)

New `doc_manager/sync/` (pure — no DB/FS; the handler feeds it rows):
- `compare.py` — `LocationSnapshot` (list of `EntryRow`: relative_path, sha256,
  text_hash, display_path) and `compare_locations(source, target, *, mode) →
  ComparisonResult`. The result carries the classified items (7.b) **and** a
  pairwise **coverage summary**: counts of `already_present` / `missing` /
  `renamed` / `conflict`, plus a source-covered percentage (how much of the source
  has an equivalent in the target).
- Directional (source = authoritative; report what the target lacks + conflicts) —
  a bidirectional view is two plans. *Open decision #2.*

Tests (unit): coverage counts over crafted snapshots; empty target → all missing;
identical snapshots → all already_present.

### 7.b — Comparison rules (relative path / hash / text)

Encoded in `compare.py`, applied per source entry against the target index:
- **already_present** — same relative path **and** same `sha256` (exact match).
- **conflict** — same relative path, **different** `sha256` (same name, different
  bytes).
- **renamed** (→ `manual_review`) — same `sha256` at a **different** relative path
  (byte-identical, renamed/moved copy).
- **text-equivalent** (→ `manual_review`) — same `text_hash` but different `sha256`
  (text-equivalent, incl. different pagination) at any path.
- **missing** (→ `copy`) — no `sha256`/`text_hash` equivalent in the target
  (proposed copy source→target).
- Precedence: exact → conflict → renamed → text-equivalent → missing. *Open
  decision #3.*

`SyncItem(action, source_relative_path, target_relative_path?, source_sha256,
target_sha256?, source_text_hash, reason)`. `reason` is a stable, safe code.

Tests (unit): each rule in isolation + precedence (a path-conflict outranks a
hash-match elsewhere); text-equivalent vs exact.

### 7.c — Persist + API + UI

- **Models + migration 0006:** `sync_plans` (source/target `source_location_id`,
  `target_location_id`, `status` `building|ready|failed`, `mode`, `item_count`,
  coverage summary JSONB, `error_code`, `built_at`, `created_at`) and
  `sync_plan_items` (plan FK cascade, `action`, source/target relative paths,
  `source_sha256`/`target_sha256`/`source_text_hash`, `reason`, `created_at`).
  **No execution columns.** Round-trip verified.
- **`build_sync_plan` handler:** load indexed entries + current file version sha256
  + content text_hash for both locations, run `compare_locations`, persist items +
  the coverage summary, set `ready` (or `failed` with a code). Plans are immutable
  once built. Register in `HANDLERS`.
- **API:** `POST /api/v1/sync-plans` (body: `source_location_id`,
  `target_location_id`, optional `mode`; Idempotency-Key; creates the `building`
  plan row + enqueues `build_sync_plan`; `202` + plan + `Location`),
  `GET /api/v1/sync-plans` (list), `GET /api/v1/sync-plans/{id}` (plan + summary),
  `GET /api/v1/sync-plans/{id}/items` (paginated, `filter[action]`). Display paths
  only; **no execute route**. *Open decision #1: 202 job vs 201 sync — leaning 202
  durable per §6.1.*
- **UI `SyncPlansPage`** (`/sync-plans`, nav): create a plan (pick source + target
  location), list plans with status + coverage, open a plan to view items grouped
  by action with **conflict highlighting** and proposed source→target paths.
  `client.ts` `createSyncPlan`/`fetchSyncPlans`/`fetchSyncPlan`/`fetchSyncPlanItems`.

Tests: PG integration — build over a crafted two-location corpus produces the right
action counts + items; endpoints (create → 202 durable + idempotent replay; get;
items pagination + action filter; unknown → 404). Vitest for the page.

### 7.d — Future-executor design doc (ADR)

`docs/adr/0006-sync-executor-boundary.md`: how a future, **separately reviewed**
executor could consume a plan — **disabled by default**, allowlisted roots,
conflict rules, checksum-after-copy verification, an audit trail, explicit
per-operation confirmation, **no automatic delete**, and never in the MVP. Records
that plans are immutable inputs and the executor is out of scope.

---

## Security posture (must hold)

- **No filesystem writes anywhere.** Comparison reads catalog hashes from
  PostgreSQL; the handler and endpoints touch no source path. The "never writes to
  source" guarantee is structural.
- **No execution surface.** There is no sync-execute endpoint, no execution
  columns, and no copy/move/delete code path in the MVP.
- Plans are **immutable** once generated; regeneration creates a new plan.
- Responses expose only server-resolved `display_path`s and relative paths — never
  `scan_root`; no route accepts a filesystem path.
- E2E test hashes source roots before/after a plan build and asserts **zero
  change**.

## Open decisions (resolve during 7.a / 7.c)

1. **`POST /sync-plans` → 202 durable job vs 201 synchronous.** Comparison is fast
   SQL, but the contract lists `/sync-plans` under idempotent job-creating POSTs
   (§6.1). Leaning **202** (`building` plan row + `build_sync_plan` job → `ready`).
2. **Directional vs bidirectional comparison.** Directional (source authoritative)
   for the MVP; a full two-way view is two plans. Leaning directional.
3. **Match precedence + whether text-equivalent is `manual_review` or its own
   action.** Leaning exact → conflict → renamed → text-equivalent → missing, all
   mapped onto the four contract actions (`copy|conflict|already_present|manual_review`).
4. **Pairwise coverage surface.** Fold the coverage summary into the plan (its
   action counts) vs a separate `GET /coverage` compare endpoint. Leaning: summary
   on the plan; the per-location `GET /coverage` (6.c) stays as-is.
5. **Which entries participate.** Indexed entries only (have `sha256` + content);
   `missing`/`failed` excluded. Confirm whether `failed` (has `sha256`, no content)
   should appear as exact/conflict candidates by file hash only.

## New dependencies

- None. Uses PostgreSQL, the durable job engine, and existing API/UI plumbing.

## Ops notes

- A sync plan build is a light SQL comparison; no model, no Qdrant, no filesystem.
- Migration `0006` adds `sync_plans` / `sync_plan_items`; upgrade/downgrade
  round-trip verified like prior migrations.
- Plans accumulate; a retention/cleanup policy for old plans is a later concern
  (they are cheap and immutable).
