# Phase 8.a — Runtime Hardening

**Status:** ✅ complete · **Branch:** `phase-8-hardening-release` · **Spec:** TECHSTACK §5.11, §14 (Phase 8.a)

Resource limits, verified graceful shutdown + stale-lease recovery, and cleanup
grace periods for rows abandoned by interrupted work.

---

## 1. Resource limits (`compose.yaml`)

Added `deploy.resources.limits` (cpus + memory) to `postgres`, `qdrant`, `api`, and
`worker`, each tunable via `.env` with sane defaults (worker gets the most headroom
because it loads the FastEmbed model):

| Service | cpus (`…_CPUS`) | memory (`…_MEM`) |
| --- | --- | --- |
| postgres | 2 | 2g |
| qdrant | 2 | 2g |
| api | 2 | 1g |
| worker | 4 | 4g |

Honored by `docker compose` v2. Worker embedding memory is further bounded by
`embedding_batch_size` (already configurable).

## 2. Graceful shutdown (verified + tested)

On SIGTERM the worker stops claiming and releases each running job with
`JobEngine.release_for_shutdown` → `retry_wait` **with the attempt count retained**
(no loss, no duplication), within `worker_shutdown_grace_seconds`. New integration
test: claim a job, release for shutdown, assert `retry_wait` + lease cleared +
`attempt_count == 1`, then a **second worker reclaims and runs it** (`attempt_count
== 2`).

## 3. Stale-lease recovery (already tested)

The reaper (`reap_expired`, run on startup + every `reaper_interval_seconds`)
reclaims expired running leases → cancel / retry_wait / fail-on-exhaustion. Covered
by existing `test_reaper_recovers_crashed_claim` and `test_stale_attempt_is_fenced`;
no change needed.

## 4. Cleanup grace periods — `gc_stale_rows`

New `JobEngine.gc_stale_rows(retention_hours)` garbage-collects rows abandoned by
interrupted work, run by a worker `_maintenance_loop` every
`maintenance_interval_seconds` (default 1 h; retention default 24 h):

- **`scan_observations`** whose owning scan job is **terminal** and older than the
  grace period — a complete scan folds and deletes its own staging, so leftovers
  belong to an interrupted/failed scan and can never reconcile.
- **`idempotency_records`** older than the retention window whose referenced job is
  terminal (or was never created) — matches contract §6.1 (retain ≥ 24 h and until
  the job is terminal).

**Never touches rows tied to open (non-terminal) work.** Idempotent; commits.
Config: `maintenance_interval_seconds` (3600), `maintenance_retention_hours` (24).

## 5. Verification

3 integration tests: GC removes **only** aged rows whose job is terminal (keeps
open-job and recent rows); GC is a no-op when nothing is stale; shutdown-release
re-queues without loss and a second worker completes it. Full backend suite **288
pass, 1 skipped**; ruff + mypy clean; `compose.yaml` validates with limits on all
four services.

## 6. Follow-ups (rest of Phase 8)

- **8.c/8.g** — complete + test the backup maintenance set and NAS flow.
- The GC could later also prune old `sync_plans` / `duplicate_*` snapshots under a
  retention policy (open decision #2); the current scope covers the safety-critical
  abandoned staging + idempotency rows.
