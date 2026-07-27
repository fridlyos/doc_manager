# Phase 6.e — Duplicates & Coverage UI

**Status:** ✅ complete · **Branch:** `phase-6-reindex-duplicates` · **Spec:** TECHSTACK §5.16, §14 (Phase 6.e)

Operator-facing screens over the Phase 6.c endpoints: browse duplicate groups with
every active path, view per-location coverage, and trigger a report rebuild.

---

## 1. Client (`client.ts`)

`fetchDuplicates(kind?)`, `fetchDuplicateGroup(id)` (group + members),
`fetchCoverage()`, and `rebuildDuplicates()` (Idempotency-Key'd
`POST /duplicates/rebuild`). Types: `DuplicateGroupInfo`, `DuplicateMemberInfo`,
`CoverageEntry`.

## 2. DuplicatesPage (`/duplicates`, in nav)

- Kind filter chips (All / Exact / Text) + a **Rebuild report** action.
- Group table: `kind` badge (exact = red, text = amber), member count, short hash.
- **Expand** a group to lazily fetch and list every member's **display_path** +
  state — so a group shows every active location/path (exit criterion 2).
- 10 s refetch; empty/loading/error states.

## 3. CoveragePage (`/coverage`, in nav)

Per-source-location table: total + counts by state (indexed / failed / unsupported
/ missing / discovered / queued), colour-coded. 10 s refetch.

Only server-resolved display paths are shown; no filesystem path is ever
constructed client-side.

## 4. Verification

3 Vitest tests: duplicates list + **expand → member paths**; **rebuild** posts to
`/duplicates/rebuild` with an Idempotency-Key; coverage renders per-location
counts by state. Full frontend suite **22 pass**; eslint + tsc clean; production
build succeeds.

## 5. Phase 6 complete

All deliverables 6.a–6.e are done. Exit criteria:

1. **Add/change/move/delete/restore converge after a scan** — reconciler (3.a) +
   scan-triggered `remove_stale_vectors` (6.d).
2. **Duplicate groups show every active location/path** — materialized report (6.c)
   surfaced here.
3. **Model/profile changes produce an explicit controlled rebuild** — reindex +
   `remove_stale_vectors` (6.b).
