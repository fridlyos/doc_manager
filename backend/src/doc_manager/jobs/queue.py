"""Durable PostgreSQL job queue (state-machine contract sections 3-8).

Every transition locks the job row, uses PostgreSQL time, and appends its
`job_events` row in the same transaction. Worker-side transitions are fenced by
a compare-and-set on ``(id, status='running', lease_owner, lease_token)`` with
an unexpired lease; zero updated rows means the attempt lost authority and must
stop (`LeaseLostError`).

The queue provides at-least-once execution. Correctness comes from fenced
leases, immutable payloads, idempotent handlers, and repairable derived stores
— not from this module alone.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.db.models import IngestionJob, IngestionJobAttempt, JobEvent
from doc_manager.db.session import db_now
from doc_manager.domain.enums import (
    TERMINAL_STATUSES,
    AttemptOutcome,
    ErrorClass,
    JobOrigin,
    JobStatus,
    JobType,
)
from doc_manager.jobs.errors import LeaseLostError

_CLAIM_SQL = text(
    """
    WITH candidate AS (
        SELECT id
        FROM ingestion_jobs
        WHERE status IN ('queued', 'retry_wait')
          AND available_at <= clock_timestamp()
          AND cancel_requested_at IS NULL
          AND attempt_count < max_attempts
          AND (:job_types_all OR job_type = ANY(CAST(:job_types AS text[])))
          AND id != ALL(CAST(:skip_ids AS uuid[]))
        ORDER BY priority DESC, available_at ASC, requested_at ASC, id ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE ingestion_jobs AS job
    SET status = 'running',
        attempt_count = job.attempt_count + 1,
        lease_owner = :worker_id,
        lease_token = :lease_token,
        heartbeat_at = clock_timestamp(),
        lease_expires_at = clock_timestamp() + make_interval(secs => :lease_seconds),
        started_at = COALESCE(job.started_at, clock_timestamp()),
        progress_phase = NULL,
        progress_current = NULL,
        progress_total = NULL,
        progress_message = NULL
    FROM candidate
    WHERE job.id = candidate.id
    RETURNING job.id
    """
)

_HEARTBEAT_SQL = text(
    """
    UPDATE ingestion_jobs
    SET heartbeat_at = clock_timestamp(),
        lease_expires_at = clock_timestamp() + make_interval(secs => :lease_seconds)
    WHERE id = :job_id
      AND status = 'running'
      AND lease_owner = :worker_id
      AND lease_token = :lease_token
      AND lease_expires_at > clock_timestamp()
    RETURNING cancel_requested_at
    """
)


def advisory_lock_key(location_id: uuid.UUID) -> int:
    """Deterministic 64-bit signed advisory-lock key for a source location."""
    digest = hashlib.sha256(str(location_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def compute_retry_delay(
    completed_attempt: int,
    *,
    base_delay_seconds: float,
    max_delay_seconds: float,
    rng: random.Random,
) -> float:
    """Equal-jitter bounded exponential backoff (contract section 6)."""
    cap = min(max_delay_seconds, base_delay_seconds * (2 ** (completed_attempt - 1)))
    return rng.uniform(cap / 2, cap)


@dataclass(frozen=True, slots=True)
class HeartbeatResult:
    alive: bool
    cancel_requested: bool


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Outcome of one claim poll.

    `job` is the claimed job, if any. `busy_job_id` reports a scan job whose
    location advisory lock is held elsewhere; the caller must skip it in this
    polling round (the attempt was rolled back, so none was consumed).
    """

    job: IngestionJob | None = None
    busy_job_id: uuid.UUID | None = None


class NotCancellableError(Exception):
    """The job is terminal (or otherwise not cancellable) — contract sec. 7."""

    def __init__(self, status: str) -> None:
        super().__init__(f"job is not cancellable in status {status}")
        self.status = status


class NotRetryableError(Exception):
    """Manual retry is valid only for failed/cancelled jobs."""

    def __init__(self, status: str) -> None:
        super().__init__(f"job is not retryable in status {status}")
        self.status = status


class JobEngine:
    """Queue operations. Stateless besides retry policy and a seedable RNG."""

    def __init__(
        self,
        *,
        base_delay_seconds: float = 5.0,
        max_delay_seconds: float = 900.0,
        rng: random.Random | None = None,
    ) -> None:
        self._base_delay = base_delay_seconds
        self._max_delay = max_delay_seconds
        self._rng = rng or random.Random()

    # ------------------------------------------------------------------ events

    async def _append_event(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        event_type: str,
        *,
        attempt_number: int | None = None,
        level: str = "info",
        actor: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        # The sequence bump shares the caller's transaction with the state
        # mutation so the UI never sees an unexplained state (contract sec. 4).
        seq = await session.scalar(
            update(IngestionJob)
            .where(IngestionJob.id == job_id)
            .values(last_event_sequence=IngestionJob.last_event_sequence + 1)
            .returning(IngestionJob.last_event_sequence)
        )
        if seq is None:
            raise LeaseLostError(f"job {job_id} vanished while appending an event")
        session.add(
            JobEvent(
                job_id=job_id,
                sequence_number=seq,
                attempt_number=attempt_number,
                event_type=event_type,
                level=level,
                actor=actor,
                message=message,
                details_json=details,
            )
        )
        await session.flush()

    # ----------------------------------------------------------------- enqueue

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        job_type: JobType,
        payload: dict[str, Any],
        origin: JobOrigin,
        source_location_id: uuid.UUID | None = None,
        catalog_entry_id: uuid.UUID | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        dedupe_key: str | None = None,
        request_key: str | None = None,
        retry_of_job_id: uuid.UUID | None = None,
        root_job_id: uuid.UUID | None = None,
        available_at: datetime | None = None,
        actor: str | None = None,
    ) -> tuple[IngestionJob, bool]:
        """Create a queued job and its first event atomically.

        Returns ``(job, coalesced)``. When a partial-uniqueness rule (open scan
        per location, dedupe key) rejects the insert, the existing open job is
        returned with ``coalesced=True`` — scheduler ticks coalesce instead of
        forming a backlog (contract sec. 8).
        """
        job = IngestionJob(
            job_type=job_type.value,
            payload_json=payload,
            origin=origin.value,
            source_location_id=source_location_id,
            catalog_entry_id=catalog_entry_id,
            priority=priority,
            max_attempts=max_attempts,
            dedupe_key=dedupe_key,
            request_key=request_key,
            retry_of_job_id=retry_of_job_id,
        )
        if available_at is not None:
            job.available_at = available_at
        nested = await session.begin_nested()
        try:
            session.add(job)
            await session.flush()
            # Root of its own lineage unless the caller linked one.
            job.root_job_id = root_job_id or job.id
            await self._append_event(
                session,
                job.id,
                "job_enqueued",
                actor=actor,
                details={"job_type": job_type.value, "origin": origin.value},
            )
            await nested.commit()
            return job, False
        except IntegrityError:
            await nested.rollback()
        existing = await self._find_open_duplicate(
            session,
            job_type=job_type,
            source_location_id=source_location_id,
            dedupe_key=dedupe_key,
            retry_of_job_id=retry_of_job_id,
        )
        if existing is None:
            raise RuntimeError("enqueue lost a uniqueness race but found no open duplicate")
        return existing, True

    async def _find_open_duplicate(
        self,
        session: AsyncSession,
        *,
        job_type: JobType,
        source_location_id: uuid.UUID | None,
        dedupe_key: str | None,
        retry_of_job_id: uuid.UUID | None,
    ) -> IngestionJob | None:
        open_statuses = [
            s.value for s in (JobStatus.queued, JobStatus.running, JobStatus.retry_wait)
        ]
        stmt = select(IngestionJob).where(IngestionJob.status.in_(open_statuses))
        if dedupe_key is not None:
            stmt = stmt.where(IngestionJob.dedupe_key == dedupe_key)
        elif retry_of_job_id is not None:
            stmt = stmt.where(IngestionJob.retry_of_job_id == retry_of_job_id)
        elif job_type is JobType.scan_location and source_location_id is not None:
            stmt = stmt.where(
                IngestionJob.job_type == job_type.value,
                IngestionJob.source_location_id == source_location_id,
            )
        else:
            return None
        found: IngestionJob | None = await session.scalar(
            stmt.limit(1).execution_options(populate_existing=True)
        )
        return found

    # ------------------------------------------------------------------- claim

    async def claim_next(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        lease_seconds: float,
        skip_ids: tuple[uuid.UUID, ...] = (),
        job_types: tuple[JobType, ...] | None = None,
    ) -> ClaimResult:
        """Fenced claim of the next due job (contract sec. 5).

        Commits the session's transaction: the attempt row and `attempt_started`
        event commit with the claim. For `scan_location` jobs a session-level
        advisory lock on the location is acquired *before* the claim commits;
        if the lock is busy the claim rolls back (no attempt consumed) and the
        job id is reported for skipping this round. Call on a session with no
        uncommitted work of its own.
        """
        lease_token = uuid.uuid4()
        try:
            claimed_id = await session.scalar(
                _CLAIM_SQL,
                {
                    "worker_id": worker_id,
                    "lease_token": lease_token,
                    "lease_seconds": lease_seconds,
                    "job_types_all": job_types is None,
                    "job_types": [t.value for t in job_types] if job_types else [],
                    "skip_ids": [str(i) for i in skip_ids],
                },
            )
            if claimed_id is None:
                await session.commit()
                return ClaimResult()
            # populate_existing: the job may already sit in this session's
            # identity map with pre-claim attributes; force a fresh read of
            # the lease fields the claim just wrote.
            job = await session.get(IngestionJob, claimed_id, populate_existing=True)
            assert job is not None
            if job.job_type == JobType.scan_location.value and job.source_location_id:
                locked = await session.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": advisory_lock_key(job.source_location_id)},
                )
                if not locked:
                    # Roll back the claim: lock collisions consume no attempt.
                    await session.rollback()
                    return ClaimResult(busy_job_id=claimed_id)
            session.add(
                IngestionJobAttempt(
                    job_id=job.id,
                    attempt_number=job.attempt_count,
                    worker_id=worker_id,
                    lease_token=lease_token,
                )
            )
            await self._append_event(
                session,
                job.id,
                "attempt_started",
                attempt_number=job.attempt_count,
                actor=worker_id,
            )
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        return ClaimResult(job=job)

    async def release_scan_lock(self, session: AsyncSession, source_location_id: uuid.UUID) -> None:
        """Release the session-level location lock at the end of a scan attempt.

        Must run on the same database connection that acquired it; connection
        or process death releases it implicitly.
        """
        await session.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": advisory_lock_key(source_location_id)},
        )

    # --------------------------------------------------------------- heartbeat

    async def heartbeat(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        lease_token: uuid.UUID,
        lease_seconds: float,
    ) -> HeartbeatResult:
        """Renew only the current unexpired lease. Zero rows = lease lost.

        Commits the session's transaction (heartbeats run on their own session).
        """
        try:
            row = (
                await session.execute(
                    _HEARTBEAT_SQL,
                    {
                        "job_id": job_id,
                        "worker_id": worker_id,
                        "lease_token": lease_token,
                        "lease_seconds": lease_seconds,
                    },
                )
            ).first()
            if row is None:
                await session.commit()
                return HeartbeatResult(alive=False, cancel_requested=False)
            await session.execute(
                update(IngestionJobAttempt)
                .where(
                    IngestionJobAttempt.job_id == job_id,
                    IngestionJobAttempt.lease_token == lease_token,
                )
                .values(last_heartbeat_at=text("clock_timestamp()"))
            )
            await session.commit()
            return HeartbeatResult(alive=True, cancel_requested=row[0] is not None)
        except BaseException:
            await session.rollback()
            raise

    # ------------------------------------------------- fenced worker transitions

    async def _fenced_transition(
        self,
        session: AsyncSession,
        job: IngestionJob,
        *,
        worker_id: str,
        lease_token: uuid.UUID,
        values: dict[str, Any],
    ) -> None:
        """CAS on (id, running, owner, token, unexpired). Raises on zero rows."""
        result = await session.execute(
            update(IngestionJob)
            .where(
                IngestionJob.id == job.id,
                IngestionJob.status == JobStatus.running.value,
                IngestionJob.lease_owner == worker_id,
                IngestionJob.lease_token == lease_token,
                IngestionJob.lease_expires_at > text("clock_timestamp()"),
            )
            .values(**values)
        )
        if getattr(result, "rowcount", 0) != 1:
            raise LeaseLostError(f"job {job.id}: fenced transition affected zero rows")

    def _cleared_lease(self) -> dict[str, Any]:
        return {
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
        }

    async def _close_attempt(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        lease_token: uuid.UUID,
        outcome: AttemptOutcome,
        *,
        error_class: str | None = None,
        error_code: str | None = None,
    ) -> None:
        await session.execute(
            update(IngestionJobAttempt)
            .where(
                IngestionJobAttempt.job_id == job_id,
                IngestionJobAttempt.lease_token == lease_token,
            )
            .values(
                finished_at=text("clock_timestamp()"),
                outcome=outcome.value,
                error_class=error_class,
                error_code=error_code,
            )
        )

    async def complete(
        self,
        session: AsyncSession,
        job: IngestionJob,
        *,
        worker_id: str,
        lease_token: uuid.UUID,
    ) -> None:
        """Publish success. Must share the transaction with the handler's final
        catalog publication so cancellation cannot interleave (contract sec. 7)."""
        await self._fenced_transition(
            session,
            job,
            worker_id=worker_id,
            lease_token=lease_token,
            values={
                "status": JobStatus.succeeded.value,
                "finished_at": text("clock_timestamp()"),
                **self._cleared_lease(),
            },
        )
        await self._close_attempt(session, job.id, lease_token, AttemptOutcome.succeeded)
        await self._append_event(
            session, job.id, "job_succeeded", attempt_number=job.attempt_count, actor=worker_id
        )

    async def fail_permanent(
        self,
        session: AsyncSession,
        job: IngestionJob,
        *,
        worker_id: str,
        lease_token: uuid.UUID,
        code: str,
        message: str,
        error_class: ErrorClass = ErrorClass.permanent,
    ) -> None:
        await self._fenced_transition(
            session,
            job,
            worker_id=worker_id,
            lease_token=lease_token,
            values={
                "status": JobStatus.failed.value,
                "finished_at": text("clock_timestamp()"),
                "error_class": error_class.value,
                "error_code": code,
                "error_message": message,
                **self._cleared_lease(),
            },
        )
        await self._close_attempt(
            session,
            job.id,
            lease_token,
            AttemptOutcome.permanent_error,
            error_class=error_class.value,
            error_code=code,
        )
        await self._append_event(
            session,
            job.id,
            "job_failed",
            attempt_number=job.attempt_count,
            level="error",
            actor=worker_id,
            details={"code": code},
        )

    async def retry_transient(
        self,
        session: AsyncSession,
        job: IngestionJob,
        *,
        worker_id: str,
        lease_token: uuid.UUID,
        code: str,
        message: str,
        error_class: ErrorClass = ErrorClass.transient,
    ) -> JobStatus:
        """Transient failure: retry with bounded backoff, or exhaust to failed."""
        exhausted = job.attempt_count >= job.max_attempts
        if exhausted:
            await self._fenced_transition(
                session,
                job,
                worker_id=worker_id,
                lease_token=lease_token,
                values={
                    "status": JobStatus.failed.value,
                    "finished_at": text("clock_timestamp()"),
                    "error_class": error_class.value,
                    "error_code": code,
                    "error_message": message,
                    **self._cleared_lease(),
                },
            )
            await self._close_attempt(
                session,
                job.id,
                lease_token,
                AttemptOutcome.transient_error,
                error_class=error_class.value,
                error_code=code,
            )
            await self._append_event(
                session,
                job.id,
                "attempts_exhausted",
                attempt_number=job.attempt_count,
                level="error",
                actor=worker_id,
            )
            await self._append_event(
                session,
                job.id,
                "job_failed",
                attempt_number=job.attempt_count,
                level="error",
                actor=worker_id,
                details={"code": code},
            )
            return JobStatus.failed
        delay = compute_retry_delay(
            job.attempt_count,
            base_delay_seconds=self._base_delay,
            max_delay_seconds=self._max_delay,
            rng=self._rng,
        )
        now = await db_now(session)
        await self._fenced_transition(
            session,
            job,
            worker_id=worker_id,
            lease_token=lease_token,
            values={
                "status": JobStatus.retry_wait.value,
                "available_at": now + timedelta(seconds=delay),
                "error_class": error_class.value,
                "error_code": code,
                "error_message": message,
                **self._cleared_lease(),
            },
        )
        await self._close_attempt(
            session,
            job.id,
            lease_token,
            AttemptOutcome.transient_error,
            error_class=error_class.value,
            error_code=code,
        )
        await self._append_event(
            session,
            job.id,
            "retry_scheduled",
            attempt_number=job.attempt_count,
            actor=worker_id,
            details={"code": code, "delay_seconds": round(delay, 3)},
        )
        return JobStatus.retry_wait

    async def acknowledge_cancel(
        self,
        session: AsyncSession,
        job: IngestionJob,
        *,
        worker_id: str,
        lease_token: uuid.UUID,
    ) -> None:
        """Worker observed a cancellation request at a safe boundary."""
        await self._fenced_transition(
            session,
            job,
            worker_id=worker_id,
            lease_token=lease_token,
            values={
                "status": JobStatus.cancelled.value,
                "finished_at": text("clock_timestamp()"),
                **self._cleared_lease(),
            },
        )
        await self._close_attempt(session, job.id, lease_token, AttemptOutcome.cancelled)
        await self._append_event(
            session, job.id, "job_cancelled", attempt_number=job.attempt_count, actor=worker_id
        )

    async def release_for_shutdown(
        self,
        session: AsyncSession,
        job: IngestionJob,
        *,
        worker_id: str,
        lease_token: uuid.UUID,
    ) -> None:
        """Graceful shutdown: immediate retry_wait, attempt count retained."""
        now = await db_now(session)
        await self._fenced_transition(
            session,
            job,
            worker_id=worker_id,
            lease_token=lease_token,
            values={
                "status": JobStatus.retry_wait.value,
                "available_at": now,
                **self._cleared_lease(),
            },
        )
        await self._close_attempt(session, job.id, lease_token, AttemptOutcome.shutdown_released)
        await self._append_event(
            session, job.id, "attempt_released", attempt_number=job.attempt_count, actor=worker_id
        )

    # ---------------------------------------------------------------- progress

    async def update_progress(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        lease_token: uuid.UUID,
        phase: str,
        current: int,
        total: int | None,
        unit: str,
        message: str | None = None,
    ) -> bool:
        """Fenced, phase-monotonic progress update (contract sec. 10)."""
        result = await session.execute(
            text(
                """
                UPDATE ingestion_jobs
                SET progress_current = CASE
                        WHEN progress_phase IS NOT DISTINCT FROM :phase
                        THEN GREATEST(COALESCE(progress_current, 0), :current)
                        ELSE :current
                    END,
                    progress_phase = :phase,
                    -- Total stays NULL while discovery is open; once known it is
                    -- non-decreasing and never below current (contract sec. 10).
                    progress_total = CASE
                        WHEN :total IS NULL THEN progress_total
                        ELSE GREATEST(:total, COALESCE(progress_total, 0), :current)
                    END,
                    progress_unit = :unit,
                    progress_message = :message,
                    progress_updated_at = clock_timestamp()
                WHERE id = :job_id
                  AND status = 'running'
                  AND lease_owner = :worker_id
                  AND lease_token = :lease_token
                  AND lease_expires_at > clock_timestamp()
                """
            ),
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "lease_token": lease_token,
                "phase": phase,
                "current": current,
                "total": total,
                "unit": unit,
                "message": message,
            },
        )
        return getattr(result, "rowcount", 0) == 1

    # ------------------------------------------------------------- API actions

    async def request_cancel(
        self, session: AsyncSession, job_id: uuid.UUID, *, actor: str
    ) -> IngestionJob:
        """Idempotent cancellation (contract sec. 7). Caller owns the transaction."""
        job = await session.scalar(
            select(IngestionJob)
            .where(IngestionJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise LookupError(str(job_id))
        if job.status == JobStatus.cancelled.value:
            # Repeating cancellation returns the terminal state without mutation.
            return job
        if job.status in {s.value for s in TERMINAL_STATUSES}:
            raise NotCancellableError(job.status)
        now = await db_now(session)
        if job.status in (JobStatus.queued.value, JobStatus.retry_wait.value):
            job.status = JobStatus.cancelled.value
            job.cancel_requested_at = job.cancel_requested_at or now
            job.cancel_requested_by = job.cancel_requested_by or actor
            job.finished_at = now
            await self._append_event(session, job.id, "job_cancelled", actor=actor)
        elif job.cancel_requested_at is None:  # running: record intent once
            job.cancel_requested_at = now
            job.cancel_requested_by = actor
            await self._append_event(session, job.id, "cancellation_requested", actor=actor)
        await session.flush()
        return job

    async def create_manual_retry(
        self,
        session: AsyncSession,
        source_job: IngestionJob,
        *,
        actor: str,
        request_key: str | None = None,
    ) -> tuple[IngestionJob, bool]:
        """New linked job for a failed/cancelled source (contract sec. 7)."""
        if source_job.status not in (JobStatus.failed.value, JobStatus.cancelled.value):
            raise NotRetryableError(source_job.status)
        return await self.enqueue(
            session,
            job_type=JobType(source_job.job_type),
            payload=source_job.payload_json,
            origin=JobOrigin.manual_retry,
            source_location_id=source_job.source_location_id,
            catalog_entry_id=source_job.catalog_entry_id,
            priority=source_job.priority,
            max_attempts=source_job.max_attempts,
            retry_of_job_id=source_job.id,
            root_job_id=source_job.root_job_id or source_job.id,
            request_key=request_key,
            actor=actor,
        )

    # ------------------------------------------------------------------ reaper

    async def reap_expired(
        self,
        session: AsyncSession,
        *,
        actor: str = "reaper",
        now: datetime | None = None,
        limit: int = 50,
    ) -> int:
        """Recover expired running leases (contract sec. 5). Returns count.

        Cancellation wins over retry; exhaustion wins over another attempt.
        `now` is injectable for tests; production uses PostgreSQL time.
        Commits the session's transaction.
        """
        reaped = 0
        try:
            effective_now = now or await db_now(session)
            rows = (
                await session.scalars(
                    select(IngestionJob)
                    .where(
                        IngestionJob.status == JobStatus.running.value,
                        IngestionJob.lease_expires_at <= effective_now,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                    .execution_options(populate_existing=True)
                )
            ).all()
            for job in rows:
                assert job.lease_token is not None
                stale_token = job.lease_token
                # Mutate the row completely BEFORE appending events: the event
                # helper runs queries which autoflush pending ORM state, and a
                # partially-mutated row would violate the lease CHECK constraint.
                events: list[tuple[str, str, dict[str, Any] | None]] = [
                    ("lease_expired", "warning", None)
                ]
                if job.cancel_requested_at is not None:
                    job.status = JobStatus.cancelled.value
                    job.finished_at = effective_now
                    events.append(("job_cancelled", "info", None))
                elif job.attempt_count >= job.max_attempts:
                    job.status = JobStatus.failed.value
                    job.finished_at = effective_now
                    job.error_class = ErrorClass.transient.value
                    job.error_code = "lease_expired"
                    job.error_message = "worker lease expired and attempts are exhausted"
                    events.append(("attempts_exhausted", "error", None))
                    events.append(("job_failed", "error", {"code": "lease_expired"}))
                else:
                    delay = compute_retry_delay(
                        job.attempt_count,
                        base_delay_seconds=self._base_delay,
                        max_delay_seconds=self._max_delay,
                        rng=self._rng,
                    )
                    job.status = JobStatus.retry_wait.value
                    job.available_at = effective_now + timedelta(seconds=delay)
                    events.append(
                        (
                            "retry_scheduled",
                            "info",
                            {"code": "lease_expired", "delay_seconds": round(delay, 3)},
                        )
                    )
                job.lease_owner = None
                job.lease_token = None
                job.lease_expires_at = None
                job.heartbeat_at = None
                await self._close_attempt(
                    session, job.id, stale_token, AttemptOutcome.lease_expired
                )
                for event_type, level, details in events:
                    await self._append_event(
                        session,
                        job.id,
                        event_type,
                        attempt_number=job.attempt_count,
                        level=level,
                        actor=actor,
                        details=details,
                    )
                reaped += 1
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        return reaped
