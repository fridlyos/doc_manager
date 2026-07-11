# ADR 0002: Use PostgreSQL as the Durable Job Queue

- **Status:** Proposed
- **Date:** 2026-07-11
- **Decision owners:** Project maintainers

## Context

Scanning, hashing, extraction, chunking, embedding, cleanup, and report generation are asynchronous and must survive process/host restarts. The MVP already requires PostgreSQL for the catalog. Introducing Redis/Celery would add another persistent service, backup surface, and operational failure mode before workload evidence justifies it.

## Decision

Store jobs and immutable job events in PostgreSQL and run a dedicated worker process using at-least-once delivery.

### Claiming and leases

- Workers claim eligible `queued` jobs in a short transaction using `SELECT ... FOR UPDATE SKIP LOCKED`.
- Claiming atomically sets `status=running`, `lease_owner`, `lease_expires_at`, `heartbeat_at`, and increments `attempt_count`.
- A worker renews its lease while executing.
- An expired lease is recoverable by a reaper only after confirming its owner is no longer healthy or the lease deadline has passed.
- Every handler is idempotent because a worker may complete an external write immediately before losing its SQL lease.

### Scheduling and retries

- Scheduled scans insert jobs only when no equivalent queued/running scan exists for that source location.
- Transient failures enter `retry_wait` with exponential backoff and bounded jitter.
- Permanent errors or exhausted attempts enter terminal `failed`.
- Manual retry creates a new job linked by `parent_job_id`; it does not rewrite terminal history.
- Cancellation is cooperative and checked at safe handler boundaries.

### Concurrency

- One location scan may be queued/running per source location.
- File indexing can run concurrently only when it cannot publish two active results for the same observed file version.
- Profile-wide rebuild and destructive cleanup operations use explicit advisory locks.

The complete lifecycle and invariants are specified in [`ingestion-job-state-machine.md`](../architecture/ingestion-job-state-machine.md).

## Consequences

### Positive

- Jobs, catalog updates, progress, and audit events share transactional infrastructure.
- No Redis/Celery service or separate backup plan is required for the MVP.
- `SKIP LOCKED` supports multiple workers when later needed.
- Operators can inspect and recover job state with normal SQL tooling.

### Negative

- PostgreSQL receives polling and event-write load.
- Queue features such as priorities, fairness, and dead-letter tooling must be implemented deliberately.
- Long work cannot hold database transactions; leasing/idempotency add application complexity.

## Alternatives considered

- **In-process background tasks:** lost on restart and cannot isolate API/worker failures.
- **Redis plus Celery/RQ:** mature queue behavior but adds a service and split operational truth too early.
- **Filesystem queue:** poor concurrency, querying, Windows/NAS semantics, and transactional behavior.
- **Message broker:** appropriate for larger distributed deployments but disproportionate for the initial single-host system.

