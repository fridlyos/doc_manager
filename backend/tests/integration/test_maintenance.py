"""Runtime hardening (Phase 8.a): stale-row GC + graceful shutdown release."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.db.models import (
    IdempotencyRecord,
    IngestionJob,
    ScanObservation,
    SourceLocation,
)
from doc_manager.domain.enums import TERMINAL_STATUSES, JobOrigin, JobStatus, JobType
from doc_manager.jobs.queue import JobEngine

pytestmark = pytest.mark.usefixtures("pg_url")


async def _job(session: AsyncSession, status: JobStatus) -> uuid.UUID:
    job = IngestionJob(
        job_type=JobType.scan_location.value,
        payload_json={"version": 1},
        origin=JobOrigin.api.value,
        status=status.value,
        max_attempts=3,
        # A terminal job requires finished_at (check constraint).
        finished_at=datetime.now(UTC) if status in TERMINAL_STATUSES else None,
    )
    session.add(job)
    await session.flush()
    job.root_job_id = job.id
    await session.flush()
    return job.id


def _observation(job_id: uuid.UUID, staged_at: datetime, path: str = "a.txt") -> ScanObservation:
    return ScanObservation(
        job_id=job_id,
        attempt_number=1,
        lease_token=uuid.uuid4(),
        relative_path=path,
        file_name=path,
        extension="txt",
        size_bytes=1,
        mtime=staged_at,
        sha256="s" * 64,
        staged_at=staged_at,
    )


async def test_gc_removes_only_abandoned_and_aged_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(hours=48)
    async with session_factory() as session:
        terminal = await _job(session, JobStatus.succeeded)
        running = await _job(session, JobStatus.queued)
        # Abandoned staging from a finished scan, aged past retention -> deleted.
        session.add(_observation(terminal, old, "old.txt"))
        # Aged, but the scan is still open -> kept.
        session.add(_observation(running, old, "open.txt"))
        # Terminal but recent -> kept (within grace).
        session.add(_observation(terminal, now, "recent.txt"))

        # Idempotency: aged + terminal job -> deleted; aged + open job -> kept;
        # recent -> kept.
        session.add_all(
            [
                IdempotencyRecord(
                    scope="s-old-terminal", fingerprint="f", job_id=terminal, created_at=old
                ),
                IdempotencyRecord(
                    scope="s-old-open", fingerprint="f", job_id=running, created_at=old
                ),
                IdempotencyRecord(
                    scope="s-recent", fingerprint="f", job_id=terminal, created_at=now
                ),
            ]
        )
        await session.commit()

    async with session_factory() as session:
        removed = await JobEngine().gc_stale_rows(session, retention_hours=24, now=now)
    assert removed == {"scan_observations": 1, "idempotency_records": 1}

    async with session_factory() as session:
        obs = (await session.scalars(select(func.count()).select_from(ScanObservation))).one()
        idem = (await session.scalars(select(func.count()).select_from(IdempotencyRecord))).one()
    assert obs == 2  # running-scan + recent kept
    assert idem == 2  # open-job + recent kept


async def test_gc_is_a_noop_when_nothing_is_stale(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        removed = await JobEngine().gc_stale_rows(session, retention_hours=24)
    assert removed == {"scan_observations": 0, "idempotency_records": 0}


async def test_shutdown_release_requeues_without_loss(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = JobEngine()
    async with session_factory() as session:
        loc = SourceLocation(name="l", scan_root="/s", display_root="/s")
        session.add(loc)
        await session.flush()
        job, _ = await engine.enqueue(
            session,
            job_type=JobType.scan_location,
            payload={"version": 1, "source_location_id": str(loc.id)},
            origin=JobOrigin.api,
            source_location_id=loc.id,
        )
        await session.commit()
        job_id = job.id

    # Claim it (running), then release for graceful shutdown.
    async with session_factory() as session:
        claim = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert claim.job is not None and claim.job.id == job_id
        token = claim.job.lease_token
        assert token is not None
        await engine.release_for_shutdown(session, claim.job, worker_id="w1", lease_token=token)
        await session.commit()

    async with session_factory() as session:
        job = await session.get(IngestionJob, job_id)
        assert job is not None
        assert job.status == JobStatus.retry_wait.value
        assert job.lease_owner is None and job.lease_token is None
        assert job.attempt_count == 1  # attempt retained, not lost

    # A second worker reclaims and can run it — no duplication, no loss.
    async with session_factory() as session:
        again = await engine.claim_next(session, worker_id="w2", lease_seconds=60)
        assert again.job is not None and again.job.id == job_id
        assert again.job.attempt_count == 2
