"""Scheduler tick: due-location selection and coalescing against real PostgreSQL."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from doc_manager.db.models import IngestionJob, SourceLocation
from doc_manager.domain.enums import JobOrigin, JobStatus
from doc_manager.jobs.queue import JobEngine
from doc_manager.jobs.scheduler import tick

pytestmark = pytest.mark.usefixtures("pg_url")


async def _add_location(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    name: str,
    enabled: bool = True,
    scan_interval_minutes: int | None = 1,
    scanned_seconds_ago: int | None = None,
) -> SourceLocation:
    async with session_factory() as session:
        location = SourceLocation(
            name=name,
            scan_root=f"/sources/{name}",
            display_root=f"/sources/{name}",
            enabled=enabled,
            scan_interval_minutes=scan_interval_minutes,
        )
        session.add(location)
        if scanned_seconds_ago is not None:
            await session.flush()
            await session.execute(
                text(
                    "UPDATE source_locations"
                    " SET last_successful_scan_at ="
                    "     clock_timestamp() - make_interval(secs => :ago)"
                    " WHERE id = :id"
                ),
                {"ago": scanned_seconds_ago, "id": location.id},
            )
        await session.commit()
        return location


async def test_tick_enqueues_due_locations_and_coalesces(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    engine = JobEngine()
    never_scanned = await _add_location(session_factory, name="never-scanned")
    overdue = await _add_location(session_factory, name="overdue", scanned_seconds_ago=120)
    await _add_location(session_factory, name="fresh", scanned_seconds_ago=1)
    await _add_location(session_factory, name="disabled", enabled=False)
    await _add_location(session_factory, name="manual-only", scan_interval_minutes=None)

    assert await tick(session_factory, engine, max_attempts=3) == 2

    async with session_factory() as session:
        jobs = (
            await session.scalars(
                select(IngestionJob).where(IngestionJob.origin == JobOrigin.scheduler.value)
            )
        ).all()
    assert {job.source_location_id for job in jobs} == {never_scanned.id, overdue.id}
    assert all(job.status == JobStatus.queued.value for job in jobs)

    # A second tick coalesces onto the open scans instead of forming a backlog.
    assert await tick(session_factory, engine, max_attempts=3) == 0

    async with session_factory() as session:
        last_tick = await session.scalar(
            text("SELECT last_tick_at FROM scheduler_state WHERE id = 1")
        )
    assert last_tick is not None
