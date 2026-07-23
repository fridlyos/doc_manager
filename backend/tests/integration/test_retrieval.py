"""End-to-end retrieval over real PostgreSQL + in-memory Qdrant (Phase 4.d).

Drives scan→index for a synthetic corpus (offline fake embedder), then exercises
RetrievalService directly: a golden query retrieves the expected document
(exit criterion 2), filters constrain candidates, and a moved file still resolves
its current path from PostgreSQL. Generation providers are never involved
(exit criterion 3).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.core.config import Settings
from doc_manager.domain.enums import JobOrigin, JobType
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import JobError
from doc_manager.jobs.handlers import HANDLERS
from doc_manager.jobs.queue import JobEngine
from doc_manager.retrieval import RetrievalService, SearchFilters
from doc_manager.vectors import QdrantRepository

pytestmark = pytest.mark.usefixtures("pg_url")


@pytest.fixture
def idx_env(
    tmp_path: Path, pg_url: str, monkeypatch: pytest.MonkeyPatch, vector_env: object
) -> object:
    settings = Settings(database_url=pg_url, artifact_root=tmp_path / "artifacts")
    monkeypatch.setattr("doc_manager.jobs.handlers.index_file.get_settings", lambda: settings)
    return vector_env


def _service(vector_env: object) -> RetrievalService:
    collection = vector_env.embedding.profile.collection_name("doc_chunks")  # type: ignore[attr-defined]
    repo = QdrantRepository(vector_env.client, collection=collection)  # type: ignore[attr-defined]
    return RetrievalService(vector_env.embedding, repo)  # type: ignore[attr-defined]


async def _make_location(
    session_factory: async_sessionmaker[AsyncSession], root: Path
) -> uuid.UUID:
    from doc_manager.db.models import SourceLocation

    async with session_factory() as session:
        loc = SourceLocation(
            name=f"loc-{uuid.uuid4().hex[:8]}",
            scan_root=str(root),
            display_root=str(root),
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
    while await _run_one(engine, db_engine) is not None:
        pass


async def test_golden_query_retrieves_expected_document(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    (tmp_path / "contract.txt").write_text("the renewal clause covers december terms")
    (tmp_path / "recipe.txt").write_text("mix flour sugar butter and bake slowly")
    location = await _make_location(session_factory, tmp_path)
    await _scan_and_index(JobEngine(), db_engine, session_factory, location)

    service = _service(idx_env)
    async with session_factory() as session:
        results = await service.search(
            session, query="the renewal clause covers december terms", top_k=5
        )
    assert results, "expected at least one hit"
    top = results[0]
    assert top.paths, "hit must resolve a current path"
    assert top.paths[0].display_path.endswith("contract.txt")
    assert top.availability == "current"
    assert top.paths[0].is_primary


async def test_filter_by_extension_constrains_candidates(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    (tmp_path / "a.txt").write_text("shared token alpha only here")
    (tmp_path / "b.md").write_text("shared token bravo only here")
    location = await _make_location(session_factory, tmp_path)
    await _scan_and_index(JobEngine(), db_engine, session_factory, location)

    service = _service(idx_env)
    async with session_factory() as session:
        md_only = await service.search(
            session,
            query="shared token",
            filters=SearchFilters(extensions=["md"]),
            top_k=10,
        )
    assert md_only, "md document should match"
    for result in md_only:
        assert all(p.display_path.endswith(".md") for p in result.paths)


async def test_empty_filter_set_returns_no_results(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    (tmp_path / "a.txt").write_text("some indexed content here")
    location = await _make_location(session_factory, tmp_path)
    await _scan_and_index(JobEngine(), db_engine, session_factory, location)

    service = _service(idx_env)
    async with session_factory() as session:
        # A source-location filter that matches nothing short-circuits to empty.
        results = await service.search(
            session,
            query="some indexed content",
            filters=SearchFilters(source_location_ids=[uuid.uuid4()]),
            top_k=5,
        )
    assert results == []


async def test_moved_file_resolves_current_path(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    original = tmp_path / "before.txt"
    original.write_text("the renewal clause covers december terms")
    location = await _make_location(session_factory, tmp_path)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location)

    # Rename (same bytes) and rescan: reconcile records a move, keeping the content
    # object; the citation must reflect the new path resolved from PostgreSQL.
    original.rename(tmp_path / "after.txt")
    await _scan_and_index(engine, db_engine, session_factory, location)

    service = _service(idx_env)
    async with session_factory() as session:
        results = await service.search(
            session, query="the renewal clause covers december terms", top_k=5
        )
    assert results
    assert results[0].paths[0].display_path.endswith("after.txt")
