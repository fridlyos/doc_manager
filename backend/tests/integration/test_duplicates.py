"""build_duplicate_report over a real indexed corpus (Phase 6.c).

Exact duplicates share a file hash; text duplicates share normalized text across
distinct file hashes (incl. whitespace-only byte differences). Rebuild is a full
idempotent replace.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.core.config import Settings
from doc_manager.db.models import DuplicateGroup, DuplicateMember, SourceLocation
from doc_manager.domain.enums import JobOrigin, JobType
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


async def _build_report(engine: JobEngine, db_engine: AsyncEngine) -> None:
    async with AsyncSession(bind=db_engine) as session:
        await engine.enqueue(
            session,
            job_type=JobType.build_duplicate_report,
            payload={"version": 1},
            origin=JobOrigin.api,
        )
        await session.commit()
    assert await _run_one(engine, db_engine) == JobType.build_duplicate_report.value


async def test_report_groups_exact_and_text_duplicates(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    # Exact pair (identical bytes) and text pair (same normalized text, diff bytes).
    (tmp_path / "c.txt").write_text("exact duplicate content")
    (tmp_path / "d.txt").write_text("exact duplicate content")
    (tmp_path / "a.txt").write_text("hello world")
    (tmp_path / "b.txt").write_text("hello   world\n")  # whitespace-only byte diff
    location = await _make_location(session_factory, tmp_path)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location)
    await _build_report(engine, db_engine)

    async with session_factory() as session:
        groups = (await session.scalars(select(DuplicateGroup))).all()
        by_kind = {g.kind: g for g in groups}
        assert set(by_kind) == {"exact", "text"}
        assert by_kind["exact"].member_count == 2
        assert by_kind["text"].member_count == 2

        exact_members = (
            await session.scalars(
                select(DuplicateMember).where(DuplicateMember.group_id == by_kind["exact"].id)
            )
        ).all()
        assert {Path(m.display_path).name for m in exact_members} == {"c.txt", "d.txt"}
        assert len({m.sha256 for m in exact_members}) == 1  # identical bytes

        text_members = (
            await session.scalars(
                select(DuplicateMember).where(DuplicateMember.group_id == by_kind["text"].id)
            )
        ).all()
        assert {Path(m.display_path).name for m in text_members} == {"a.txt", "b.txt"}
        assert len({m.sha256 for m in text_members}) == 2  # distinct bytes, same text


async def test_rebuild_is_idempotent(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    (tmp_path / "a.txt").write_text("same bytes")
    (tmp_path / "b.txt").write_text("same bytes")
    location = await _make_location(session_factory, tmp_path)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location)

    await _build_report(engine, db_engine)
    await _build_report(engine, db_engine)  # second run replaces, not appends

    async with session_factory() as session:
        groups = (await session.scalars(select(func.count()).select_from(DuplicateGroup))).one()
        members = (await session.scalars(select(func.count()).select_from(DuplicateMember))).one()
    assert groups == 1
    assert members == 2
