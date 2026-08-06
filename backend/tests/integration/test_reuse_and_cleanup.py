"""Canonical reuse invariant + delete/restore vector convergence (Phase 6.d).

- Structure-equivalent files share one content object → shared citation chunks.
- Text-equivalent files with different pagination get separate content objects →
  chunks are NOT shared.
- Deleting every copy converges the store: remove_stale_vectors retires the
  orphaned content's chunks/points; a restore re-indexes and reconverges.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.core.config import Settings
from doc_manager.db.models import CatalogEntry, Chunk, ContentObject, SourceLocation
from doc_manager.domain.enums import CatalogEntryState, JobOrigin, JobType
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
    # remove_stale_vectors resolves its own profile + repo; point them at the fakes.
    monkeypatch.setattr(
        "doc_manager.jobs.handlers.cleanup.resolve_embedding_profile",
        lambda s: vector_env.embedding.profile,  # type: ignore[attr-defined]
    )
    monkeypatch.setattr(
        "doc_manager.jobs.handlers.cleanup.build_qdrant_repository",
        lambda s, p: vector_env.repository(s),  # type: ignore[attr-defined]
    )
    return vector_env


def _pdf(path: Path, pages: list[str]) -> None:
    doc = pymupdf.open()
    for body in pages:
        doc.new_page().insert_text((72, 72), body)
    doc.save(path)
    doc.close()


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


async def test_structure_equivalent_files_share_one_content_object(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    _pdf(tmp_path / "a.pdf", ["alpha beta gamma"])
    _pdf(tmp_path / "b.pdf", ["alpha beta gamma"])  # identical structure + text
    location = await _make_location(session_factory, tmp_path)
    await _scan_and_index(JobEngine(), db_engine, session_factory, location)

    async with session_factory() as session:
        content = (await session.scalars(select(ContentObject))).all()
        assert len(content) == 1  # reused across both paths
        chunk_contents = {c.content_object_id for c in (await session.scalars(select(Chunk))).all()}
        assert chunk_contents == {content[0].id}  # both share the same chunks


async def test_text_equivalent_different_pagination_do_not_share_chunks(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    # Same words, different page splits -> same text_hash, different structure_hash.
    _pdf(tmp_path / "one_page.pdf", ["alpha beta gamma delta"])
    _pdf(tmp_path / "two_page.pdf", ["alpha beta", "gamma delta"])
    location = await _make_location(session_factory, tmp_path)
    await _scan_and_index(JobEngine(), db_engine, session_factory, location)

    async with session_factory() as session:
        content = (await session.scalars(select(ContentObject))).all()
        assert len(content) == 2  # separate content objects for citation correctness
        assert len({c.text_hash for c in content}) == 1  # same normalized text
        assert len({c.structure_hash for c in content}) == 2  # different pagination
        chunk_contents = [c.content_object_id for c in (await session.scalars(select(Chunk))).all()]
        # Chunks belong to distinct content objects — not shared.
        assert set(chunk_contents) == {content[0].id, content[1].id}


async def _count_points(vector_env: object) -> int:
    from qdrant_client import AsyncQdrantClient

    client: AsyncQdrantClient = vector_env.client  # type: ignore[attr-defined]
    collection = vector_env.embedding.profile.collection_name("doc_chunks")  # type: ignore[attr-defined]
    if not await client.collection_exists(collection):
        return 0
    return (await client.count(collection)).count


async def test_delete_converges_and_restore_reconverges(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    idx_env: object,
) -> None:
    doc = tmp_path / "doc.txt"
    doc.write_text("unique deletable content here")
    location = await _make_location(session_factory, tmp_path)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location)

    async with session_factory() as session:
        assert (await session.scalars(select(func.count()).select_from(ContentObject))).one() == 1
        assert (await session.scalars(select(func.count()).select_from(Chunk))).one() >= 1
    assert await _count_points(idx_env) >= 1

    # Delete the file, rescan -> entry missing -> scan enqueues remove_stale_vectors.
    doc.unlink()
    await _scan_and_index(engine, db_engine, session_factory, location)

    async with session_factory() as session:
        entry = (await session.scalars(select(CatalogEntry))).one()
        assert entry.state == CatalogEntryState.missing.value
        assert (await session.scalars(select(func.count()).select_from(ContentObject))).one() == 0
        assert (await session.scalars(select(func.count()).select_from(Chunk))).one() == 0
    assert await _count_points(idx_env) == 0

    # Restore the file, rescan -> missing becomes discovered -> re-index rebuilds.
    doc.write_text("unique deletable content here")
    await _scan_and_index(engine, db_engine, session_factory, location)

    async with session_factory() as session:
        entry = (await session.scalars(select(CatalogEntry))).one()
        assert entry.state == CatalogEntryState.indexed.value
        assert (await session.scalars(select(func.count()).select_from(ContentObject))).one() == 1
        assert (await session.scalars(select(func.count()).select_from(Chunk))).one() >= 1
    assert await _count_points(idx_env) >= 1
