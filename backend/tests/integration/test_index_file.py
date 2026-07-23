"""index_file pipeline: scan enqueues, extraction persists content + versions."""

from __future__ import annotations

import uuid
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.core.config import Settings
from doc_manager.db.models import CatalogEntry, Chunk, ContentObject, FileVersion, SourceLocation
from doc_manager.domain.enums import (
    CatalogEntryState,
    ExtractionStatus,
    JobOrigin,
    JobType,
)
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import JobError
from doc_manager.jobs.handlers import HANDLERS
from doc_manager.jobs.queue import JobEngine

pytestmark = pytest.mark.usefixtures("pg_url")


@pytest.fixture
def artifact_root(
    tmp_path: Path, pg_url: str, monkeypatch: pytest.MonkeyPatch, vector_env: object
) -> Path:
    # vector_env patches the embedding + Qdrant builders to offline fakes.
    root = tmp_path / "artifacts"
    settings = Settings(database_url=pg_url, artifact_root=root)
    monkeypatch.setattr("doc_manager.jobs.handlers.index_file.get_settings", lambda: settings)
    return root


def _pdf(path: Path, pages: list[str]) -> None:
    doc = pymupdf.open()
    for body in pages:
        doc.new_page().insert_text((72, 72), body)
    doc.save(path)
    doc.close()


async def _make_location(
    session_factory: async_sessionmaker[AsyncSession],
    root: Path,
    *,
    include: list[str] | None = None,
) -> SourceLocation:
    async with session_factory() as session:
        location = SourceLocation(
            name=f"loc-{uuid.uuid4().hex[:8]}",
            scan_root=str(root),
            display_root=str(root),
            include_extensions=include or [],
        )
        session.add(location)
        await session.commit()
        return location


async def _scan_and_index(
    engine: JobEngine,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    location_id: uuid.UUID,
) -> None:
    """Enqueue a scan, then run it and every index job it enqueues to completion."""
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


async def _run_one(engine: JobEngine, db_engine: AsyncEngine) -> str | None:
    """Claim and run a single job through its registered handler."""
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
                # Handler committed the document-level outcome before raising;
                # the real worker marks the job failed here.
                pass
            finally:
                if job.job_type == JobType.scan_location.value and job.source_location_id:
                    await engine.release_scan_lock(session, job.source_location_id)
            return job.job_type
        finally:
            await session.close()


async def _drain(engine: JobEngine, db_engine: AsyncEngine) -> None:
    while await _run_one(engine, db_engine) is not None:
        pass


async def _entries(session: AsyncSession, location_id: uuid.UUID) -> dict[str, CatalogEntry]:
    rows = (
        await session.scalars(
            select(CatalogEntry).where(CatalogEntry.source_location_id == location_id)
        )
    ).all()
    return {e.relative_path: e for e in rows}


async def test_scan_enqueues_and_index_persists_content(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_root: Path,
) -> None:
    (tmp_path / "notes.md").write_text("# Title\n\nBody paragraph.")
    _pdf(tmp_path / "doc.pdf", ["Page one words", "Page two words"])
    location = await _make_location(session_factory, tmp_path)

    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location.id)

    async with session_factory() as session:
        entries = await _entries(session, location.id)
        assert {"notes.md", "doc.pdf"} <= set(entries)
        for entry in entries.values():
            assert entry.state == CatalogEntryState.indexed.value
            assert entry.current_file_version_id is not None

        versions = (await session.scalars(select(FileVersion))).all()
        assert len(versions) == 2
        assert all(v.extraction_status == ExtractionStatus.extracted.value for v in versions)
        assert all(v.content_object_id is not None for v in versions)

        content = (await session.scalars(select(ContentObject))).all()
        assert len(content) == 2
        for co in content:
            assert (artifact_root / co.artifact_path).exists()


async def test_encrypted_pdf_records_failed_document(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_root: Path,
) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    doc.save(
        tmp_path / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="pw", user_pw="pw"
    )
    doc.close()
    location = await _make_location(session_factory, tmp_path)

    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location.id)

    async with session_factory() as session:
        entry = (await _entries(session, location.id))["locked.pdf"]
        assert entry.state == CatalogEntryState.failed.value
        version = await session.scalar(
            select(FileVersion).where(FileVersion.catalog_entry_id == entry.id)
        )
        assert version is not None
        assert version.extraction_status == ExtractionStatus.failed.value
        assert version.error_code == "encrypted"
        assert version.content_object_id is None


async def test_unsupported_extension_is_marked_unsupported(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_root: Path,
) -> None:
    (tmp_path / "data.xml").write_text("<root/>")
    # include xml so the scanner observes it, though no extractor is registered.
    location = await _make_location(session_factory, tmp_path, include=["xml"])

    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location.id)

    async with session_factory() as session:
        entry = (await _entries(session, location.id))["data.xml"]
        assert entry.state == CatalogEntryState.unsupported.value
        version = await session.scalar(
            select(FileVersion).where(FileVersion.catalog_entry_id == entry.id)
        )
        assert version is not None
        assert version.extraction_status == ExtractionStatus.unsupported.value


async def test_identical_files_share_one_content_object(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_root: Path,
) -> None:
    (tmp_path / "a.txt").write_text("the same exact content")
    (tmp_path / "b.txt").write_text("the same exact content")
    location = await _make_location(session_factory, tmp_path)

    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location.id)

    async with session_factory() as session:
        content = (await session.scalars(select(ContentObject))).all()
        # Same structure hash + profile -> one reused content object for both.
        assert len(content) == 1
        versions = (await session.scalars(select(FileVersion))).all()
        assert len(versions) == 2
        assert {v.content_object_id for v in versions} == {content[0].id}
        # Chunks/points are keyed on the content object, so the duplicate reuses
        # them — one set, embedded once.
        chunk_rows = (
            await session.scalars(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.content_object_id == content[0].id)
            )
        ).one()
        assert chunk_rows >= 1


async def _count_points(client: object, collection: str) -> int:
    from qdrant_client import AsyncQdrantClient

    assert isinstance(client, AsyncQdrantClient)
    if not await client.collection_exists(collection):
        return 0
    return (await client.count(collection)).count


async def test_indexing_persists_chunks_and_points(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_root: Path,
    vector_env: object,
) -> None:
    (tmp_path / "doc.txt").write_text("the renewal clause covers december terms")
    location = await _make_location(session_factory, tmp_path)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location.id)

    collection = vector_env.embedding.profile.collection_name("doc_chunks")  # type: ignore[attr-defined]
    async with session_factory() as session:
        chunks = (await session.scalars(select(Chunk))).all()
        assert len(chunks) >= 1
        assert all(c.token_count > 0 for c in chunks)
        # One vector point per SQL chunk row.
        assert await _count_points(vector_env.client, collection) == len(chunks)  # type: ignore[attr-defined]


async def test_reindex_creates_no_duplicate_chunks_or_points(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_root: Path,
    vector_env: object,
) -> None:
    (tmp_path / "doc.txt").write_text("alpha beta gamma delta epsilon zeta")
    location = await _make_location(session_factory, tmp_path)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location.id)

    collection = vector_env.embedding.profile.collection_name("doc_chunks")  # type: ignore[attr-defined]
    async with session_factory() as session:
        first_chunks = (await session.scalars(select(func.count()).select_from(Chunk))).one()
    first_points = await _count_points(vector_env.client, collection)  # type: ignore[attr-defined]

    # Re-index the same, unchanged file: deterministic ids -> idempotent upsert.
    await _scan_and_index(engine, db_engine, session_factory, location.id)

    async with session_factory() as session:
        second_chunks = (await session.scalars(select(func.count()).select_from(Chunk))).one()
    second_points = await _count_points(vector_env.client, collection)  # type: ignore[attr-defined]
    assert (second_chunks, second_points) == (first_chunks, first_points)
