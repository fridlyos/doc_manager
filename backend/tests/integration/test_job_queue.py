"""State-machine acceptance scenarios against real PostgreSQL (contract sec. 12).

Engine methods run inside the session's (auto-begun) transaction; tests commit
explicitly so reads and writes never fight over `Session.begin()`.
"""

from __future__ import annotations

import asyncio
import random
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.db.models import (
    IngestionJob,
    IngestionJobAttempt,
    JobEvent,
    SourceLocation,
)
from doc_manager.db.session import db_now
from doc_manager.domain.enums import JobOrigin, JobStatus, JobType
from doc_manager.jobs.errors import LeaseLostError
from doc_manager.jobs.queue import (
    JobEngine,
    compute_retry_delay,
)

pytestmark = pytest.mark.usefixtures("pg_url")


def make_engine(seed: int = 42) -> JobEngine:
    return JobEngine(base_delay_seconds=5.0, max_delay_seconds=900.0, rng=random.Random(seed))


async def enqueue_job(
    session: AsyncSession,
    engine: JobEngine,
    *,
    job_type: JobType = JobType.index_file,
    max_attempts: int = 3,
    source_location_id: uuid.UUID | None = None,
) -> IngestionJob:
    job, _ = await engine.enqueue(
        session,
        job_type=job_type,
        payload={"version": 1},
        origin=JobOrigin.api,
        max_attempts=max_attempts,
        source_location_id=source_location_id,
    )
    await session.commit()
    return job


async def expire_lease(
    session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID
) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE ingestion_jobs SET lease_expires_at ="
                " clock_timestamp() - interval '1 second' WHERE id = :id"
            ),
            {"id": job_id},
        )
        await session.commit()


async def make_due(session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> None:
    async with session_factory() as session:
        await session.execute(
            text("UPDATE ingestion_jobs SET available_at = clock_timestamp() WHERE id = :id"),
            {"id": job_id},
        )
        await session.commit()


async def events_of(session: AsyncSession, job_id: uuid.UUID) -> list[str]:
    rows = await session.scalars(
        select(JobEvent.event_type)
        .where(JobEvent.job_id == job_id)
        .order_by(JobEvent.sequence_number)
    )
    return list(rows)


async def test_enqueue_claim_and_complete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        job = await enqueue_job(session, engine)
        claim = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert claim.job is not None and claim.job.id == job.id
        assert claim.job.status == JobStatus.running.value
        assert claim.job.attempt_count == 1
        assert claim.job.lease_token is not None
        await engine.complete(session, claim.job, worker_id="w1", lease_token=claim.job.lease_token)
        await session.commit()
        await session.refresh(job)
        assert job.status == JobStatus.succeeded.value
        assert job.lease_token is None and job.finished_at is not None
        assert await events_of(session, job.id) == [
            "job_enqueued",
            "attempt_started",
            "job_succeeded",
        ]
        attempt = await session.scalar(
            select(IngestionJobAttempt).where(IngestionJobAttempt.job_id == job.id)
        )
        assert attempt is not None and attempt.outcome == "succeeded"


async def test_competing_claims_yield_one_winner(
    db_engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        job = await enqueue_job(session, engine)

    async def claim_once() -> uuid.UUID | None:
        async with session_factory() as s:
            result = await engine.claim_next(
                s, worker_id=f"w-{uuid.uuid4().hex[:4]}", lease_seconds=60
            )
            return result.job.id if result.job else None

    winners = [w for w in await asyncio.gather(claim_once(), claim_once()) if w]
    assert winners == [job.id]
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(IngestionJobAttempt)
            .where(IngestionJobAttempt.job_id == job.id)
        )
        assert count == 1


async def test_stale_attempt_is_fenced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        job = await enqueue_job(session, engine)
        first = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert first.job is not None
        stale_token = first.job.lease_token
        assert stale_token is not None

    await expire_lease(session_factory, job.id)
    async with session_factory() as session:
        assert await engine.reap_expired(session) == 1
    await make_due(session_factory, job.id)

    async with session_factory() as session:
        second = await engine.claim_next(session, worker_id="w2", lease_seconds=60)
        assert second.job is not None and second.job.attempt_count == 2

    # Attempt 1 must be unable to heartbeat, publish, or complete.
    async with session_factory() as session:
        hb = await engine.heartbeat(
            session, job_id=job.id, worker_id="w1", lease_token=stale_token, lease_seconds=60
        )
        assert hb.alive is False
    async with session_factory() as session:
        with pytest.raises(LeaseLostError):
            await engine.complete(session, second.job, worker_id="w1", lease_token=stale_token)
        await session.rollback()
    # The current attempt keeps its authority.
    async with session_factory() as session:
        fresh = await session.get(IngestionJob, job.id)
        assert fresh is not None and fresh.status == JobStatus.running.value
        assert fresh.attempt_count == 2


async def test_transient_backoff_uses_seeded_jitter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine(seed=7)
    async with session_factory() as session:
        job = await enqueue_job(session, engine)
        claim = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert claim.job is not None and claim.job.lease_token is not None
        before = await db_now(session)
        status = await engine.retry_transient(
            session,
            claim.job,
            worker_id="w1",
            lease_token=claim.job.lease_token,
            code="nas_timeout",
            message="t",
        )
        await session.commit()
        assert status == JobStatus.retry_wait
        await session.refresh(job)
        expected = compute_retry_delay(
            1, base_delay_seconds=5.0, max_delay_seconds=900.0, rng=random.Random(7)
        )
        actual = (job.available_at - before).total_seconds()
        assert abs(actual - expected) < 0.5
        assert 2.4 <= actual <= 5.6  # equal-jitter bounds for attempt 1
        assert job.status == JobStatus.retry_wait.value
        # Not claimable before available_at.
        early = await engine.claim_next(session, worker_id="w2", lease_seconds=60)
        assert early.job is None


async def test_permanent_failure_is_terminal(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        job = await enqueue_job(session, engine)
        claim = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert claim.job is not None and claim.job.lease_token is not None
        await engine.fail_permanent(
            session,
            claim.job,
            worker_id="w1",
            lease_token=claim.job.lease_token,
            code="path_escape",
            message="bad",
        )
        await session.commit()
        await session.refresh(job)
        assert job.status == JobStatus.failed.value
        assert job.error_code == "path_escape"
        nothing = await engine.claim_next(session, worker_id="w2", lease_seconds=60)
        assert nothing.job is None


async def test_attempt_exhaustion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        job = await enqueue_job(session, engine, max_attempts=1)
        claim = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert claim.job is not None and claim.job.lease_token is not None
        status = await engine.retry_transient(
            session,
            claim.job,
            worker_id="w1",
            lease_token=claim.job.lease_token,
            code="nas_timeout",
            message="t",
        )
        await session.commit()
        assert status == JobStatus.failed
        await session.refresh(job)
        assert job.status == JobStatus.failed.value
        events = await events_of(session, job.id)
        assert "attempts_exhausted" in events and "job_failed" in events


async def test_cancel_before_claim_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        job = await enqueue_job(session, engine)
        await engine.request_cancel(session, job.id, actor="api")
        await session.commit()
        again = await engine.request_cancel(session, job.id, actor="api")
        await session.commit()
        assert again.status == JobStatus.cancelled.value
        events = await events_of(session, job.id)
        assert events.count("job_cancelled") == 1
        nothing = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert nothing.job is None


async def test_cancel_during_running_flows_through_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        job = await enqueue_job(session, engine)
        claim = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert claim.job is not None and claim.job.lease_token is not None
        cancelled = await engine.request_cancel(session, job.id, actor="api")
        await session.commit()
        # Intent flag only: stored status remains running (contract sec. 2).
        assert cancelled.status == JobStatus.running.value
        assert cancelled.cancel_requested_at is not None

    async with session_factory() as session:
        hb = await engine.heartbeat(
            session,
            job_id=job.id,
            worker_id="w1",
            lease_token=claim.job.lease_token,
            lease_seconds=60,
        )
        assert hb.alive and hb.cancel_requested

    async with session_factory() as session:
        await engine.acknowledge_cancel(
            session, claim.job, worker_id="w1", lease_token=claim.job.lease_token
        )
        await session.commit()
        fresh = await session.get(IngestionJob, job.id)
        assert fresh is not None and fresh.status == JobStatus.cancelled.value
        # Repeating cancellation on a cancelled job is a no-op returning the
        # terminal state (contract sec. 7); no extra event is appended.
        repeat = await engine.request_cancel(session, job.id, actor="api")
        await session.commit()
        assert repeat.status == JobStatus.cancelled.value
        events = await events_of(session, job.id)
        assert events.count("job_cancelled") == 1


async def test_reaper_recovers_crashed_claim(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        job = await enqueue_job(session, engine)
        claim = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert claim.job is not None  # worker "crashes" here
    await expire_lease(session_factory, job.id)
    async with session_factory() as session:
        assert await engine.reap_expired(session) == 1
        fresh = await session.get(IngestionJob, job.id)
        assert fresh is not None and fresh.status == JobStatus.retry_wait.value
        events = await events_of(session, job.id)
        assert "lease_expired" in events and "retry_scheduled" in events
    await make_due(session_factory, job.id)
    async with session_factory() as session:
        second = await engine.claim_next(session, worker_id="w2", lease_seconds=60)
        assert second.job is not None and second.job.attempt_count == 2


async def test_manual_retry_lineage_and_single_open_child(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        job = await enqueue_job(session, engine, max_attempts=1)
        claim = await engine.claim_next(session, worker_id="w1", lease_seconds=60)
        assert claim.job is not None and claim.job.lease_token is not None
        await engine.fail_permanent(
            session,
            claim.job,
            worker_id="w1",
            lease_token=claim.job.lease_token,
            code="bad_input",
            message="x",
        )
        await session.commit()
        child, coalesced = await engine.create_manual_retry(session, claim.job, actor="api")
        await session.commit()
        assert not coalesced
        assert child.id != job.id
        assert child.retry_of_job_id == job.id
        assert child.root_job_id == job.id
        assert child.attempt_count == 0 and child.status == JobStatus.queued.value
        # A second retry request coalesces onto the open child.
        child2, coalesced2 = await engine.create_manual_retry(session, claim.job, actor="api")
        await session.commit()
        assert coalesced2 and child2.id == child.id


async def test_scan_enqueue_coalesces_per_location(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = make_engine()
    async with session_factory() as session:
        location = SourceLocation(name="loc-a", scan_root="/sources/a", display_root="/sources/a")
        session.add(location)
        await session.commit()
        first, c1 = await engine.enqueue(
            session,
            job_type=JobType.scan_location,
            payload={"version": 1, "source_location_id": str(location.id)},
            origin=JobOrigin.api,
            source_location_id=location.id,
        )
        await session.commit()
        second, c2 = await engine.enqueue(
            session,
            job_type=JobType.scan_location,
            payload={"version": 1, "source_location_id": str(location.id)},
            origin=JobOrigin.scheduler,
            source_location_id=location.id,
        )
        await session.commit()
        assert not c1 and c2
        assert first.id == second.id
