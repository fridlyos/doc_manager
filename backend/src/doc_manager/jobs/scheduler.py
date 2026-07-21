"""Periodic scheduler: enqueue due location scans.

The one-open-scan-per-location partial unique index makes concurrent ticks
coalesce onto the existing job instead of forming a backlog (state-machine
contract section 8), so the scheduler needs no distributed lock.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from doc_manager.core.logging import get_logger
from doc_manager.db.models import SourceLocation
from doc_manager.domain.enums import JobOrigin, JobType
from doc_manager.jobs.queue import JobEngine

log = get_logger("doc_manager.jobs.scheduler")

_DUE_LOCATIONS = select(SourceLocation).where(
    SourceLocation.enabled.is_(True),
    SourceLocation.scan_interval_minutes.is_not(None),
    text(
        "(last_successful_scan_at IS NULL"
        " OR last_successful_scan_at"
        # Column is bigint; make_interval only accepts int4.
        "    + make_interval(mins => scan_interval_minutes::int) <= clock_timestamp())"
    ),
)


async def tick(
    session_factory: async_sessionmaker[AsyncSession],
    engine: JobEngine,
    *,
    max_attempts: int,
) -> int:
    """Enqueue scans for every due location. Returns jobs enqueued (not coalesced)."""
    enqueued = 0
    async with session_factory() as session, session.begin():
        due = (await session.scalars(_DUE_LOCATIONS)).all()
        for location in due:
            _, coalesced = await engine.enqueue(
                session,
                job_type=JobType.scan_location,
                payload={"version": 1, "source_location_id": str(location.id)},
                origin=JobOrigin.scheduler,
                source_location_id=location.id,
                max_attempts=max_attempts,
                actor="scheduler",
            )
            if not coalesced:
                enqueued += 1
        await session.execute(
            text(
                "INSERT INTO scheduler_state (id, last_tick_at)"
                " VALUES (1, clock_timestamp())"
                " ON CONFLICT (id) DO UPDATE SET last_tick_at = clock_timestamp()"
            )
        )
    if enqueued:
        log.info("scheduler_enqueued_scans", count=enqueued)
    return enqueued
