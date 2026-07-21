"""scan_location handler scenarios: happy path, missing/restore, unsafe roots."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from doc_manager.db.models import CatalogEntry, IngestionJob, ScanObservation, SourceLocation
from doc_manager.domain.enums import CatalogEntryState, JobOrigin, JobStatus, JobType
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import TransientJobError
from doc_manager.jobs.handlers.scan_location import handle_scan_location
from doc_manager.jobs.queue import JobEngine

pytestmark = pytest.mark.usefixtures("pg_url")


def build_corpus(root: Path) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.pdf").write_bytes(b"%PDF-fake")
    (root / "docs" / "b.txt").write_text("hello")
    (root / "notes.md").write_text("# notes")
    (root / "ignore.tmp").write_text("skip me")  # unsupported extension
    (root / ".docman-source-id").write_text("corpus-01\n")


async def create_location(
    session: AsyncSession, root: Path, *, name: str = "test-loc"
) -> SourceLocation:
    location = SourceLocation(name=name, scan_root=str(root), display_root=str(root))
    session.add(location)
    await session.commit()
    return location


async def run_scan(
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    location_id: uuid.UUID,
) -> IngestionJob:
    """Enqueue + claim + execute one scan attempt the way the worker does."""
    engine = JobEngine()
    async with session_factory() as session:
        job, _ = await engine.enqueue(
            session,
            job_type=JobType.scan_location,
            payload={"version": 1, "source_location_id": str(location_id)},
            origin=JobOrigin.api,
            source_location_id=location_id,
        )
        await session.commit()
    async with db_engine.connect() as conn:
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            claim = await engine.claim_next(
                session,
                worker_id="scan-w",
                lease_seconds=60,
                job_types=(JobType.scan_location,),
            )
            assert claim.job is not None and claim.job.lease_token is not None
            ctx = JobContext(
                session=session,
                engine=engine,
                job=claim.job,
                worker_id="scan-w",
                lease_token=claim.job.lease_token,
                lease_seconds=60,
            )
            try:
                await handle_scan_location(ctx)
            finally:
                if claim.job.source_location_id is not None:
                    await engine.release_scan_lock(session, claim.job.source_location_id)
            return claim.job
        finally:
            await session.close()


async def entries_by_path(session: AsyncSession, location_id: uuid.UUID) -> dict[str, CatalogEntry]:
    rows = (
        await session.scalars(
            select(CatalogEntry).where(CatalogEntry.source_location_id == location_id)
        )
    ).all()
    return {e.relative_path: e for e in rows}


async def test_scan_happy_path(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_corpus(tmp_path)
    async with session_factory() as session:
        location = await create_location(session, tmp_path)
    job = await run_scan(db_engine, session_factory, location.id)

    async with session_factory() as session:
        fresh_job = await session.get(IngestionJob, job.id)
        assert fresh_job is not None and fresh_job.status == JobStatus.succeeded.value
        entries = await entries_by_path(session, location.id)
        assert set(entries) == {"docs/a.pdf", "docs/b.txt", "notes.md"}
        for entry in entries.values():
            assert entry.state == CatalogEntryState.discovered.value
            assert entry.last_observed_size_bytes is not None
        fresh_loc = await session.get(SourceLocation, location.id)
        assert fresh_loc is not None
        assert fresh_loc.last_successful_scan_at is not None
        # First successful scan adopts the observed sentinel.
        assert fresh_loc.sentinel_id == "corpus-01"
        # Staging is cleaned after reconciliation.
        staged = (await session.scalars(select(ScanObservation))).all()
        assert staged == []


async def test_scan_missing_and_restore(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_corpus(tmp_path)
    async with session_factory() as session:
        location = await create_location(session, tmp_path)
    await run_scan(db_engine, session_factory, location.id)

    (tmp_path / "notes.md").unlink()
    await run_scan(db_engine, session_factory, location.id)
    async with session_factory() as session:
        entries = await entries_by_path(session, location.id)
        assert entries["notes.md"].state == CatalogEntryState.missing.value
        assert entries["notes.md"].missing_since is not None
        assert entries["docs/a.pdf"].state == CatalogEntryState.discovered.value

    (tmp_path / "notes.md").write_text("# notes again")
    await run_scan(db_engine, session_factory, location.id)
    async with session_factory() as session:
        entries = await entries_by_path(session, location.id)
        assert entries["notes.md"].state == CatalogEntryState.discovered.value
        assert entries["notes.md"].missing_since is None


async def test_unreachable_root_never_marks_missing(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_corpus(tmp_path)
    async with session_factory() as session:
        location = await create_location(session, tmp_path)
    await run_scan(db_engine, session_factory, location.id)

    # Point the location at a root that no longer exists.
    async with session_factory() as session:
        await session.execute(
            text("UPDATE source_locations SET scan_root = :p WHERE id = :i"),
            {"p": str(tmp_path / "gone"), "i": location.id},
        )
        await session.commit()

    with pytest.raises(TransientJobError) as excinfo:
        await run_scan(db_engine, session_factory, location.id)
    assert excinfo.value.code == "source_unavailable"

    async with session_factory() as session:
        entries = await entries_by_path(session, location.id)
        # An unreachable root must never mark unseen files missing.
        assert all(e.state == CatalogEntryState.discovered.value for e in entries.values())


async def test_sentinel_mismatch_is_transient(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_corpus(tmp_path)
    async with session_factory() as session:
        location = await create_location(session, tmp_path)
    await run_scan(db_engine, session_factory, location.id)  # adopts corpus-01

    # The mapped drive now points at the wrong share: sentinel differs.
    (tmp_path / ".docman-source-id").write_text("some-other-share\n")
    with pytest.raises(TransientJobError) as excinfo:
        await run_scan(db_engine, session_factory, location.id)
    assert excinfo.value.code == "source_unavailable"
    async with session_factory() as session:
        entries = await entries_by_path(session, location.id)
        assert all(e.state == CatalogEntryState.discovered.value for e in entries.values())


async def test_exclude_globs_and_extension_filter(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_corpus(tmp_path)
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "wip.md").write_text("draft")
    async with session_factory() as session:
        location = SourceLocation(
            name="filtered",
            scan_root=str(tmp_path),
            display_root=str(tmp_path),
            include_extensions=["md"],
            exclude_globs=["drafts/*"],
        )
        session.add(location)
        await session.commit()
    await run_scan(db_engine, session_factory, location.id)
    async with session_factory() as session:
        entries = await entries_by_path(session, location.id)
        assert set(entries) == {"notes.md"}


async def test_rename_is_detected_as_move(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_corpus(tmp_path)
    async with session_factory() as session:
        location = await create_location(session, tmp_path)
    await run_scan(db_engine, session_factory, location.id)
    async with session_factory() as session:
        original_id = (await entries_by_path(session, location.id))["notes.md"].id

    # Rename without changing content: same bytes at a new path.
    (tmp_path / "notes.md").rename(tmp_path / "renamed.md")
    await run_scan(db_engine, session_factory, location.id)

    async with session_factory() as session:
        entries = await entries_by_path(session, location.id)
        assert "notes.md" not in entries
        assert "renamed.md" in entries
        # Same catalog row retargeted — not a new add plus a missing old entry.
        assert entries["renamed.md"].id == original_id
        assert entries["renamed.md"].state != CatalogEntryState.missing.value


async def test_mtime_touch_does_not_requeue_but_edit_does(
    tmp_path: Path,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_corpus(tmp_path)
    async with session_factory() as session:
        location = await create_location(session, tmp_path)
    await run_scan(db_engine, session_factory, location.id)

    # Pretend the file was already indexed, then only touch its mtime.
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE catalog_entries SET state = 'indexed'"
                " WHERE source_location_id = :i AND relative_path = 'notes.md'"
            ),
            {"i": location.id},
        )
        await session.commit()
    future = datetime(2030, 1, 1, tzinfo=UTC).timestamp()
    os.utime(tmp_path / "notes.md", (future, future))
    await run_scan(db_engine, session_factory, location.id)
    async with session_factory() as session:
        entry = (await entries_by_path(session, location.id))["notes.md"]
        # Identical bytes: no re-index, indexed state preserved.
        assert entry.state == CatalogEntryState.indexed.value

    # Now actually edit the content: it must be requeued.
    (tmp_path / "notes.md").write_text("# notes, materially changed")
    await run_scan(db_engine, session_factory, location.id)
    async with session_factory() as session:
        entry = (await entries_by_path(session, location.id))["notes.md"]
        assert entry.state == CatalogEntryState.discovered.value
