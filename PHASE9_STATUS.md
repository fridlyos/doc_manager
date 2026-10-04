# Phase 9 Progress Status

**Branch:** `phase-9-ui-scan-progress` (cut from `main` after Phase 8 merge).

Scope = TECHSTACK §14 "Phase 9: UI Modification and Scan Progress Experience" —
turn the feature-complete backend into a complete operator UI centred on a live
scan-progress view. Scanning stays a durable background worker job driven through
the API; it does not move into the browser.

## Deliverables

| # | Deliverable | Status |
| --- | --- | --- |
| 9.a | Backend scan-progress API (breakdown, target, serialization, filter) | ✅ complete |
| 9.b | Reusable UI primitives + API client control surface | ✅ complete |
| 9.c | Live scan-progress view + job console | ✅ complete |
| 9.d | UI states, accessibility, responsive, provider indicators | ✅ complete |
| 9.e | Browser E2E (Playwright) | ✅ complete |

Delivered via decisions: **Playwright** real-browser E2E, **polling** transport
(no jobs SSE), **sub-phased** commits.

## What landed

- **9.a** — `ingestion_jobs.progress_detail_json` (migration 0007) +
  `DOCMAN_SCAN_TARGET_FILES` (10000). `JobEngine.update_progress_detail`
  (lease-fenced, best-effort); the scan handler writes discovered/scanned/target
  during staging and the reconcile breakdown atomically at completion, and links
  `index_file` children via `root_job_id`. `serialize_job` exposes
  `progress.updated_at` + `progress.detail`; `GET /jobs/{id}` adds a `scan_summary`
  (persisted detail + live index-child aggregate); `GET /jobs` gains
  `filter[source_location_id]`. Backend tests for the breakdown + aggregation +
  filter.
- **9.b** — components `Modal` (focus-trap + restore), `ProgressBar`, `StatusPill`,
  `CountsRow`, `ConfirmDialog`; `FolderPickerModal` refactored onto `Modal`.
  Client: enriched `Job`/`ScanSummary` types; `fetchJob`, `fetchJobs(filters)`,
  `cancelJob`, `retryJob`, `patchLocation`, `reindexLocation`, `testLocation`.
  Polling pauses on hidden tab.
- **9.c** — `ScanProgress` (finds a location's latest scan, polls its detail while
  active; scanned/target bar + breakdown tiles + elapsed + source_unavailable-aware
  error; refresh-reconnect by construction). LocationsPage per-row controls
  (enable/disable, scan, re-index, test, delete via `ConfirmDialog`, schedule
  editor) + inline panel. JobsPage console (status/type filters, progress column,
  cancel/retry).
- **9.d** — header `ProviderBadge` (local/external/search-only); skip-link;
  `aria-live` on polled Status/Jobs/scan-progress; `--disabled` contrast nudged to
  AA; responsive header/nav/tables.
- **9.e** — Playwright harness (Chromium, mocked API via route interception, no
  backend needed); smoke pass over all screens; scan submit → live progress →
  refresh/reconnect → completed final counts → cancel.

## Verification

- Backend: ruff/format/mypy clean; `make test` 232 passed / 100 skipped (the
  PG-backed scan-progress tests skip without compose PG).
- Frontend: `tsc` + eslint + prettier clean; vitest **24/24** (src);
  Playwright **14/14** in Chromium (`npm run test:e2e`).

## Pending live-stack validation (not runnable here: no Docker/PG)

The browser E2E proves the UI against a mocked API. Before calling the MVP
released, run against a real stack:

- the 9.a PG-backed tests (`test_scan_handler` breakdown, `test_api_phase2`
  filter/scan_summary);
- a real synthetic-corpus scan end to end (add location → scan-now → watch
  `scanned/target` + the breakdown advance → correct final counts + explicit
  success), plus the Phase 8 DoD E2E + a performance baseline row.

## Known follow-ups

- 9.f deep cross-links (citation ↔ document, job ↔ source record) are partial —
  the views exist; richer link-throughs are a polish follow-up.
- Automated axe accessibility pass (8.i backlog) still deferred.
