# Phase 7.c — Persisted Dry-Run Sync Plans

**Status:** ✅ complete · **Branch:** `phase-7-sync-planning` · **Spec:** TECHSTACK §5.14, §6, §8; §14 (Phase 7.c)

Persists and displays immutable, read-only dry-run sync plans built by the 7.a/7.b
comparison engine. **No execution surface anywhere** — the feature compares and
plans only, and building a plan never writes to a source root.

---

## 1. Model + migration 0006

- **`sync_plans`** — `source_location_id` / `target_location_id` (FK, cascade),
  `status` (`building | ready | failed`), `item_count`, `covered_percent`,
  `summary_json` (per-action counts), `error_code`, `built_at`, `created_at`.
- **`sync_plan_items`** — `plan_id` (FK, cascade), `action`, `reason`, source
  relative path + sha256 + text_hash, matched target relative path + sha256
  (nullable for a `copy`).

**No execution columns.** Migration `0006` create/drop verified with an upgrade →
downgrade → re-upgrade round-trip; all conftest truncation lists updated.

## 2. `build_sync_plan` handler

Registered for `JobType.build_sync_plan`. Loads each location's **indexed** entries
+ current file-version sha256 + content text_hash into a `LocationSnapshot`, runs
`compare_locations` (7.a/7.b), persists the classified items + the coverage summary,
and sets the plan `ready`. The comparison uses **catalog hashes only** — the handler
opens no file, so source roots are structurally untouched.

## 3. API

- **`POST /api/v1/sync-plans`** — body `{source_location_id, target_location_id}`;
  Idempotency-Key; rejects `source == target` (422) and unknown locations (404);
  creates the `building` plan row + enqueues `build_sync_plan`; `202` + plan +
  `Location: /sync-plans/{id}`; idempotent replay returns the same plan
  (open decision #1 resolved: **202 durable job**).
- **`GET /api/v1/sync-plans`** — keyset-paginated list (newest first).
- **`GET /api/v1/sync-plans/{id}`** — plan + coverage summary.
- **`GET /api/v1/sync-plans/{id}/items`** — paginated, `filter[action]`.
- **No execute/apply route exists** (asserted by test).

## 4. SyncPlansPage (`/sync-plans`, in nav)

Create a plan (source + target location selects), list plans with status +
covered% + item count, and expand a **ready** plan to view items grouped by action
with **conflict / copy highlighting** and proposed `source → target` paths. A
notice states no files are moved/copied/deleted. `client.ts`
`createSyncPlan`/`fetchSyncPlans`/`fetchSyncPlan`/`fetchSyncPlanItems`.

## 5. Verification

- **Handler + no-write E2E (PG + Qdrant), 2 tests:** a crafted two-location corpus
  yields the right actions (`keep→already_present`, `moved→manual_review`,
  `clash→conflict`, `new→copy`) + summary; **fingerprints (sha256 + mtime) of both
  source roots are identical before and after the build** — proving exit criterion 2;
  empty target → all `copy`.
- **Endpoints (PG, TestClient), 7 tests:** create → 202 durable + idempotent replay;
  same-source/target → 422; unknown location → 404; missing key → 400; get plan +
  items + action filter; unknown → 404; and **no execute/apply route** (404/405).
- **UI (Vitest), 2 tests:** list + expand items (conflict/copy); Compare posts
  `{source, target}` with an Idempotency-Key.

Gate: backend **285 pass, 1 skipped**; frontend **24 pass**; ruff/mypy/eslint/tsc
clean; migration 0006 round-trips; production build succeeds.

## 6. Follow-ups

- **7.d** — the future-executor boundary ADR (disabled by default, allowlisted
  roots, checksum-after-copy, audit, explicit confirm, no auto-delete) closes the
  phase.
