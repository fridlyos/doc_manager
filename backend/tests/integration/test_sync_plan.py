"""build_sync_plan over a real two-location corpus (Phase 7.c).

Verifies the classified items + coverage, and — the key safety property — that
building a plan never writes to either source root.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.core.config import Settings
from doc_manager.db.models import SourceLocation, SyncPlan, SyncPlanItem
from doc_manager.domain.enums import JobOrigin, JobType, SyncPlanStatus
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


def _fingerprint(root: Path) -> dict[str, str]:
    """Map of relative path -> sha256+mtime, to prove nothing under root changed."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            out[str(p.relative_to(root))] = f"{digest}:{p.stat().st_mtime_ns}"
    return out


async def _build_plan(
    engine: JobEngine,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    source: uuid.UUID,
    target: uuid.UUID,
) -> uuid.UUID:
    async with session_factory() as session:
        plan = SyncPlan(
            source_location_id=source,
            target_location_id=target,
            status=SyncPlanStatus.building.value,
        )
        session.add(plan)
        await session.flush()
        plan_id = plan.id
        await engine.enqueue(
            session,
            job_type=JobType.build_sync_plan,
            payload={"version": 1, "plan_id": str(plan_id)},
            origin=JobOrigin.api,
        )
        await session.commit()
    assert await _run_one(engine, db_engine) == JobType.build_sync_plan.value
    return plan_id


async def test_plan_classifies_and_never_writes_source_roots(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    src_root = tmp_path / "source"
    tgt_root = tmp_path / "target"
    src_root.mkdir()
    tgt_root.mkdir()
    # keep: exact match; moved: renamed; clash: conflict; new: missing.
    (src_root / "keep.txt").write_text("keep me identical")
    (tgt_root / "keep.txt").write_text("keep me identical")
    (src_root / "moved.txt").write_text("renamed content here")
    (tgt_root / "archive/moved.txt").parent.mkdir()
    (tgt_root / "archive/moved.txt").write_text("renamed content here")
    (src_root / "clash.txt").write_text("source version of clash")
    (tgt_root / "clash.txt").write_text("target version of clash")
    (src_root / "new.txt").write_text("only in the source")

    source = await _make_location(session_factory, src_root)
    target = await _make_location(session_factory, tgt_root)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, source)
    await _scan_and_index(engine, db_engine, session_factory, target)

    before_src = _fingerprint(src_root)
    before_tgt = _fingerprint(tgt_root)

    plan_id = await _build_plan(engine, db_engine, session_factory, source, target)

    # Source roots are byte-and-mtime identical after the dry run.
    assert _fingerprint(src_root) == before_src
    assert _fingerprint(tgt_root) == before_tgt

    async with session_factory() as session:
        plan = await session.get(SyncPlan, plan_id)
        assert plan is not None and plan.status == SyncPlanStatus.ready.value
        assert plan.item_count == 4
        assert plan.summary_json == {
            "total_source": 4,
            "already_present": 1,
            "copy": 1,
            "conflict": 1,
            "manual_review": 1,
            "covered": 2,
        }
        actions = {
            i.source_relative_path: i.action
            for i in (
                await session.scalars(select(SyncPlanItem).where(SyncPlanItem.plan_id == plan_id))
            ).all()
        }
    assert actions == {
        "keep.txt": "already_present",
        "moved.txt": "manual_review",
        "clash.txt": "conflict",
        "new.txt": "copy",
    }


async def test_plan_over_empty_target_is_all_copy(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    src_root = tmp_path / "s"
    tgt_root = tmp_path / "t"
    src_root.mkdir()
    tgt_root.mkdir()
    (src_root / "a.txt").write_text("alpha")
    source = await _make_location(session_factory, src_root)
    target = await _make_location(session_factory, tgt_root)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, source)
    await _scan_and_index(engine, db_engine, session_factory, target)

    plan_id = await _build_plan(engine, db_engine, session_factory, source, target)
    async with session_factory() as session:
        plan = await session.get(SyncPlan, plan_id)
        assert plan is not None
        assert plan.covered_percent == 0.0
        count = (
            await session.scalars(
                select(func.count())
                .select_from(SyncPlanItem)
                .where(SyncPlanItem.plan_id == plan_id)
            )
        ).one()
    assert count == 1
