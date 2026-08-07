# Phase 7.a / 7.b — Location Comparison & Pairwise Coverage

**Status:** ✅ complete · **Branch:** `phase-7-sync-planning` · **Spec:** TECHSTACK §5.14, §14 (Phase 7.a, 7.b)

The pure comparison engine: classify a source location's entries against a target
by relative path, file hash, and normalized text hash, and summarize pairwise
coverage. Coverage (7.a) and the comparison rules (7.b) are one tested library;
Phase 7.c feeds it catalog rows and persists the result.

---

## 1. Why one library

Pairwise coverage counts *are* the classification counts, so the coverage summary
(7.a) and the path/hash/text rules (7.b) are the same pure module. No DB, no
filesystem — the `build_sync_plan` handler (7.c) loads catalog rows and calls it.
There is no execution path anywhere: this only compares.

## 2. `sync/compare.py`

`compare_locations(source, target) → ComparisonResult` (items + coverage).

**Actions** (the four contract values) with precedence — a **path** match wins over
a **hash** match elsewhere, which wins over a **text-equivalent**, which wins over
**missing**:

| Condition | Action | reason |
| --- | --- | --- |
| same relative path + same sha256 | `already_present` | `exact_match` |
| same relative path, different sha256 | `conflict` | `path_hash_mismatch` |
| same sha256 at a different path | `manual_review` | `renamed` |
| same text_hash, different bytes | `manual_review` | `text_equivalent` |
| no equivalent in target | `copy` | `missing_in_target` |

Each `SyncItem` carries the source relative path + hashes and the matched target
relative path/sha256 (or `None` for a copy). Reason codes are stable and safe (no
paths beyond the item's own fields).

**Determinism:** among equivalent target candidates (same hash/text at multiple
paths) the lowest relative path is chosen, so a plan is reproducible.

**Coverage** (`CoverageSummary`): `total_source` and per-action counts; `covered =
already_present + manual_review` (source entries with a content equivalent in the
target); `covered_percent` (0.0 when the source is empty — no divide error).

## 3. Directional by design

The source is authoritative; the report says what the target is *missing*, what
*conflicts*, and what already *matches*. A bidirectional view is two comparisons
(open decision #2 — directional for the MVP).

## 4. Verification

11 unit tests (pure/offline): each rule in isolation (exact, conflict, renamed,
text-equivalent, missing); **precedence** (a path conflict outranks a hash match
elsewhere); deterministic renamed pick (lowest path); coverage counts + percent;
identical → 100%; empty target → all missing; empty source → 0% (no divide error).

Gate: full backend suite **276 pass, 1 skipped**; ruff + mypy clean.

## 5. Follow-ups (7.c / 7.d)

- **7.c** — `sync_plans` / `sync_plan_items` (migration 0006), `build_sync_plan`
  handler feeding this library indexed-entry rows and persisting items + the
  coverage summary, and the `POST/GET /sync-plans[/items]` API + `SyncPlansPage` UI.
  An E2E test hashes source roots before/after and asserts zero change.
- **7.d** — the future-executor boundary ADR.
