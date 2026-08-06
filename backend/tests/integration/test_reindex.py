"""Bulk re-index fan-out (Phase 6.a): the parent job enqueues one deduped
index_file per eligible entry, and a re-index creates no duplicate chunks/points.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.core.config import Settings
from doc_manager.db.models import CatalogEntry, Chunk, IngestionJob, SourceLocation
from doc_manager.domain.enums import CatalogEntryState, JobOrigin, JobStatus, JobType
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import JobError
from doc_manager.jobs.handlers import HANDLERS
from doc_manager.jobs.queue import JobEngine

pytestmark = pytest.mark.usefixtures("pg_url")


@pytest.fixture
def idx_env(
    tmp_path: Path, pg_url: str, monkeypatch: pytest.MonkeyPatch, vector_env: object
) -> object:
    settings = Settings(database_url=pg_url, artifact_root=tmp_path / "artifacts")
    monkeypatch.setattr("doc_manager.jobs.handlers.index_file.get_settings", lambda: settings)
    return vector_env


async def _make_location(
    session_factory: async_sessionmaker[AsyncSession], root: Path
) -> uuid.UUID:
    async with session_factory() as session:
        loc = SourceLocation(
            name=f"loc-{uuid.uuid4().hex[:8]}", scan_root=str(root), display_root=str(root)
        )
        session.add(loc)
        await session.commit()
        return loc.id


async def _run_one(engine: JobEngine, db_engine: AsyncEngine) -> str | None:
    async with db_engine.connect() as conn:
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            claim = await engine.claim_next(session, worker_id="w", lease_seconds=60)
            if claim.job is None:
                return None
            job = claim.job
            assert job.lease_token is not None
            ctx = JobContext(
                session=session,
                engine=engine,
                job=job,
                worker_id="w",
                lease_token=job.lease_token,
                lease_seconds=60,
            )
            try:
                await HANDLERS[JobType(job.job_type)](ctx)
            except JobError:
                pass
            finally:
                if job.job_type == JobType.scan_location.value and job.source_location_id:
                    await engine.release_scan_lock(session, job.source_location_id)
            return str(job.job_type)
        finally:
            await session.close()


async def _drain(engine: JobEngine, db_engine: AsyncEngine) -> None:
    while await _run_one(engine, db_engine) is not None:
        pass


async def _scan_and_index(
    engine: JobEngine,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    location_id: uuid.UUID,
) -> None:
    async with session_factory() as session:
        await engine.enqueue(
            session,
            job_type=JobType.scan_location,
            payload={"version": 1, "source_location_id": str(location_id)},
            origin=JobOrigin.api,
            source_location_id=location_id,
        )
        await session.commit()
    await _drain(engine, db_engine)


async def test_location_reindex_fans_out_and_stays_idempotent(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    (tmp_path / "a.txt").write_text("alpha content here")
    (tmp_path / "b.txt").write_text("bravo content there")
    location = await _make_location(session_factory, tmp_path)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location)

    async with session_factory() as session:
        entries = (
            await session.scalars(
                select(CatalogEntry).where(CatalogEntry.source_location_id == location)
            )
        ).all()
        assert {e.state for e in entries} == {CatalogEntryState.indexed.value}
        first_chunks = (await session.scalars(select(func.count()).select_from(Chunk))).one()

    # Enqueue the bulk reindex parent and run just that job.
    async with session_factory() as session:
        await engine.enqueue(
            session,
            job_type=JobType.reindex_all_for_profile,
            payload={"version": 1, "scope": "location", "source_location_id": str(location)},
            origin=JobOrigin.api,
            source_location_id=location,
        )
        await session.commit()

    # Run the parent (fan-out), then confirm it enqueued an index_file per entry.
    ran = await _run_one(engine, db_engine)
    assert ran == JobType.reindex_all_for_profile.value

    async with session_factory() as session:
        pending_index = (
            await session.scalars(
                select(func.count())
                .select_from(IngestionJob)
                .where(
                    IngestionJob.job_type == JobType.index_file.value,
                    IngestionJob.status == JobStatus.queued.value,
                )
            )
        ).one()
    assert pending_index == 2  # one per eligible entry

    # Drain the children; re-index of unchanged content must not duplicate chunks.
    await _drain(engine, db_engine)
    async with session_factory() as session:
        entries = (
            await session.scalars(
                select(CatalogEntry).where(CatalogEntry.source_location_id == location)
            )
        ).all()
        assert {e.state for e in entries} == {CatalogEntryState.indexed.value}
        second_chunks = (await session.scalars(select(func.count()).select_from(Chunk))).one()
    assert second_chunks == first_chunks


async def test_reindex_scope_all_covers_every_location(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    root1 = tmp_path / "r1"
    root2 = tmp_path / "r2"
    root1.mkdir()
    root2.mkdir()
    (root1 / "x.txt").write_text("one")
    (root2 / "y.txt").write_text("two")
    loc1 = await _make_location(session_factory, root1)
    loc2 = await _make_location(session_factory, root2)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, loc1)
    await _scan_and_index(engine, db_engine, session_factory, loc2)

    async with session_factory() as session:
        await engine.enqueue(
            session,
            job_type=JobType.reindex_all_for_profile,
            payload={"version": 1, "scope": "all"},
            origin=JobOrigin.api,
            dedupe_key="reindex:all",
        )
        await session.commit()
    assert await _run_one(engine, db_engine) == JobType.reindex_all_for_profile.value

    async with session_factory() as session:
        enqueued = (
            await session.scalars(
                select(func.count())
                .select_from(IngestionJob)
                .where(
                    IngestionJob.job_type == JobType.index_file.value,
                    IngestionJob.status == JobStatus.queued.value,
                )
            )
        ).one()
    assert enqueued == 2  # one per entry across both locations
