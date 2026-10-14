"""index_file pipeline: scan enqueues, extraction persists content + versions."""

from __future__ import annotations

import uuid
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.core.config import Settings
from doc_manager.db.models import (
    CatalogEntry,
    Chunk,
    ContentObject,
    FileVersion,
    IngestionJob,
    SourceLocation,
)
from doc_manager.domain.enums import (
    CatalogEntryState,
    ExtractionStatus,
    JobOrigin,
    JobStatus,
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


async def test_broken_pdf_is_never_extracted_again(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from doc_manager.jobs.handlers import index_file as index_mod

    calls: list[str] = []
    real_get_extractor = index_mod.get_extractor

    def counting_get_extractor(extension: str, **kwargs: object) -> object:
        calls.append(extension)
        return real_get_extractor(extension, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(index_mod, "get_extractor", counting_get_extractor)

    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    location = await _make_location(session_factory, tmp_path)
    engine = JobEngine()
    await _scan_and_index(engine, db_engine, session_factory, location.id)
    assert calls == ["pdf"]

    async def reindex(entry_id: uuid.UUID) -> None:
        async with session_factory() as session:
            await engine.enqueue(
                session,
                job_type=JobType.index_file,
                payload={"version": 1, "catalog_entry_id": str(entry_id)},
                origin=JobOrigin.api,
                catalog_entry_id=entry_id,
            )
            await session.commit()
        await _drain(engine, db_engine)

    async with session_factory() as session:
        entry = (await _entries(session, location.id))["broken.pdf"]
        assert entry.state == CatalogEntryState.failed.value

    # Explicit retry of the same file: refused without opening it.
    await reindex(entry.id)
    # A copy with identical bytes under another name: also refused.
    (tmp_path / "broken copy.pdf").write_bytes((tmp_path / "broken.pdf").read_bytes())
    await _scan_and_index(engine, db_engine, session_factory, location.id)
    assert calls == ["pdf"]

    async with session_factory() as session:
        entries = await _entries(session, location.id)
        assert entries["broken copy.pdf"].state == CatalogEntryState.failed.value
        codes = (
            await session.scalars(
                select(FileVersion.error_code).where(
                    FileVersion.catalog_entry_id.in_([e.id for e in entries.values()])
                )
            )
        ).all()
    assert set(codes) == {"malformed"}

    # Changed content is a different file: it gets a fresh attempt. (The harness
    # leaves failed jobs open where the real worker would close them; close them
    # so the rescan's index job is not coalesced onto a dead one.)
    async with session_factory() as session:
        running = (
            await session.scalars(
                select(IngestionJob).where(IngestionJob.status == JobStatus.running.value)
            )
        ).all()
        for job in running:
            assert job.lease_token is not None
            await engine.fail_permanent(
                session,
                job,
                worker_id="w",
                lease_token=job.lease_token,
                code="malformed",
                message="broken",
            )
        await session.commit()
    _pdf(tmp_path / "broken.pdf", ["now a real pdf"])
    await _scan_and_index(engine, db_engine, session_factory, location.id)
    assert calls == ["pdf", "pdf"]
    async with session_factory() as session:
        assert (await _entries(session, location.id))[
            "broken.pdf"
        ].state == CatalogEntryState.indexed.value


async def test_scanned_pdf_is_handed_to_the_low_priority_ocr_lane(
    tmp_path: Path,
    pg_url: str,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    vector_env: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from doc_manager.extraction.pdf import PdfExtractor

    settings = Settings(database_url=pg_url, artifact_root=tmp_path / "artifacts", ocr_enabled=True)
    monkeypatch.setattr("doc_manager.jobs.handlers.index_file.get_settings", lambda: settings)
    ocr_calls: list[int] = []

    def fake_ocr(self: PdfExtractor, page: pymupdf.Page) -> str:
        ocr_calls.append(page.number)
        return "lowes receipt extended protection plan"

    monkeypatch.setattr(PdfExtractor, "_ocr_page", fake_ocr)
    root = tmp_path / "docs"
    root.mkdir()
    scanned = pymupdf.open()
    scanned.new_page()  # image-only: no text layer
    scanned.save(root / "scan.pdf")
    scanned.close()
    _pdf(root / "text.pdf", ["plain text layer"])
    location = await _make_location(session_factory, root)

    await _scan_and_index(JobEngine(), db_engine, session_factory, location.id)

    async with session_factory() as session:
        entries = await _entries(session, location.id)
        assert entries["scan.pdf"].state == CatalogEntryState.indexed.value
        assert entries["text.pdf"].state == CatalogEntryState.indexed.value
        jobs = (
            await session.execute(
                select(
                    IngestionJob.job_type, IngestionJob.catalog_entry_id, IngestionJob.priority
                ).where(
                    IngestionJob.job_type.in_([JobType.index_file.value, JobType.ocr_file.value])
                )
            )
        ).all()
    by_type: dict[str, list[tuple[uuid.UUID | None, int]]] = {}
    for job_type, entry_id, priority in jobs:
        by_type.setdefault(job_type, []).append((entry_id, priority))
    # Both files go through normal indexing; only the scanned one reaches OCR,
    # at low priority, and OCR ran exactly once (in the OCR job).
    assert len(by_type["index_file"]) == 2
    assert by_type["ocr_file"] == [(entries["scan.pdf"].id, -10)]
    assert ocr_calls == [0]
