"""`index_file` handler: extract, normalize, store, and record a file version.

Phase 3 pipeline for one catalog entry (TECHSTACK 7.2, up to but not including
chunking/embedding which are Phase 4):

1. Verify the file still matches the observed SHA-256 — if it changed mid-flight,
   exit transiently without publishing (a rescan re-observes it).
2. Dispatch to an extractor by extension; an unknown type is ``unsupported``.
3. Extract + normalize; a per-document extraction error is recorded on the file
   version and the entry goes ``failed`` (visible in the error queue).
4. Reuse an existing content object when structure hash + extraction profile +
   normalization version match; otherwise write the compressed artifact and
   create one.
5. Link a ``file_versions`` row and mark the entry ``indexed`` — all in the final
   fenced transaction so a stale worker cannot publish.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sqlalchemy import select, text

from doc_manager.artifact_store import ArtifactStore
from doc_manager.core.config import get_settings
from doc_manager.core.hashing import sha256_file
from doc_manager.core.logging import get_logger
from doc_manager.db.models import (
    CatalogEntry,
    ContentObject,
    FileVersion,
    IngestionJob,
    SourceLocation,
)
from doc_manager.db.session import db_now
from doc_manager.domain.enums import CatalogEntryState, ExtractionStatus, JobStatus
from doc_manager.extraction import ExtractionError, Extractor, get_extractor
from doc_manager.extraction.normalize import NormalizedDocument, normalize
from doc_manager.extraction.profile import extraction_profile_hash
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import LeaseLostError, PermanentJobError, TransientJobError

log = get_logger("doc_manager.jobs.index_file")


async def handle_index_file(ctx: JobContext) -> None:
    session = ctx.session
    job = ctx.job
    if job.catalog_entry_id is None:
        raise PermanentJobError("bad_request", "index_file job has no catalog entry")
    entry = await session.get(CatalogEntry, job.catalog_entry_id)
    if entry is None:
        raise TransientJobError("not_found", "catalog entry no longer exists")
    location = await session.get(SourceLocation, entry.source_location_id)
    if location is None:
        raise TransientJobError("source_unavailable", "source location no longer exists")

    full = Path(location.scan_root) / entry.relative_path

    # Verify the file still matches the fingerprint that triggered indexing.
    try:
        st = await asyncio.to_thread(full.stat)
        current_sha = await asyncio.to_thread(sha256_file, full)
    except OSError as exc:
        # Unreachable/locked/vanished: transient — the scanner owns "missing".
        raise TransientJobError("source_unavailable", "file is not readable") from exc
    if current_sha != entry.sha256:
        # Changed since it was observed: don't publish mixed state; a rescan
        # will re-observe and re-enqueue (TECHSTACK 7.2).
        raise TransientJobError("fingerprint_changed", "file changed during indexing")
    ctx.check_boundary()

    extractor = get_extractor(entry.extension)
    if extractor is None:
        await _publish_terminal(
            ctx,
            entry,
            st,
            current_sha,
            status=ExtractionStatus.unsupported,
            entry_state=CatalogEntryState.unsupported,
        )
        return

    try:
        extracted = await asyncio.to_thread(extractor.extract, full)
    except ExtractionError as exc:
        await _publish_terminal(
            ctx,
            entry,
            st,
            current_sha,
            status=ExtractionStatus.failed,
            entry_state=CatalogEntryState.failed,
            error_code=exc.code.value,
            error_message=exc.message,
        )
        raise PermanentJobError(exc.code.value, exc.message) from exc

    normalized = normalize(extracted)
    profile_hash = extraction_profile_hash(extractor.name, extractor.version)
    store = ArtifactStore(get_settings().artifact_root)
    artifact = await asyncio.to_thread(
        store.store,
        normalized,
        extractor_name=extractor.name,
        extractor_version=extractor.version,
        extraction_profile_hash=profile_hash,
        metadata=extracted.metadata,
    )
    ctx.check_boundary()

    await _publish_success(
        ctx, entry, st, current_sha, normalized, extractor, profile_hash, artifact.relative_path
    )
    log.info(
        "file_indexed",
        job_id=str(job.id),
        catalog_entry_id=str(entry.id),
        pages=len(normalized.pages),
        artifact_reused=artifact.reused,
    )


async def _lock_job(ctx: JobContext) -> None:
    """Fence the final publication on the current, unexpired lease."""
    locked = await ctx.session.scalar(
        select(IngestionJob)
        .where(
            IngestionJob.id == ctx.job.id,
            IngestionJob.status == JobStatus.running.value,
            IngestionJob.lease_owner == ctx.worker_id,
            IngestionJob.lease_token == ctx.lease_token,
            IngestionJob.lease_expires_at > text("clock_timestamp()"),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise LeaseLostError(f"job {ctx.job.id}: lost lease before publishing")
    if locked.cancel_requested_at is not None:
        ctx.cancel_requested = True
        ctx.check_boundary()


async def _publish_success(
    ctx: JobContext,
    entry: CatalogEntry,
    st: os.stat_result,
    sha256: str,
    normalized: NormalizedDocument,
    extractor: Extractor,
    profile_hash: str,
    artifact_path: str,
) -> None:
    session = ctx.session
    await _lock_job(ctx)
    content = await session.scalar(
        select(ContentObject).where(
            ContentObject.structure_hash == normalized.structure_hash,
            ContentObject.extraction_profile_hash == profile_hash,
            ContentObject.normalization_version == normalized.normalization_version,
        )
    )
    if content is None:
        content = ContentObject(
            text_hash=normalized.text_hash,
            structure_hash=normalized.structure_hash,
            extractor_name=extractor.name,
            extractor_version=extractor.version,
            extraction_profile_hash=profile_hash,
            normalization_version=normalized.normalization_version,
            artifact_path=artifact_path,
            page_count=len(normalized.pages),
            character_count=normalized.character_count,
        )
        session.add(content)
        await session.flush()

    now = await db_now(session)
    version = FileVersion(
        catalog_entry_id=entry.id,
        size_bytes=st.st_size,
        mtime=entry.last_observed_mtime or now,
        sha256=sha256,
        content_object_id=content.id,
        extraction_status=ExtractionStatus.extracted.value,
        indexed_at=now,
    )
    session.add(version)
    await session.flush()

    entry.current_file_version_id = version.id
    entry.state = CatalogEntryState.indexed.value
    await ctx.engine.complete(
        session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
    )
    await session.commit()


async def _publish_terminal(
    ctx: JobContext,
    entry: CatalogEntry,
    st: os.stat_result,
    sha256: str,
    *,
    status: ExtractionStatus,
    entry_state: CatalogEntryState,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Record an unsupported/failed outcome as the entry's current version.

    Unsupported completes the job (nothing more to do); failed commits the error
    and leaves the job for the worker to mark permanently failed.
    """
    session = ctx.session
    await _lock_job(ctx)
    now = await db_now(session)
    version = FileVersion(
        catalog_entry_id=entry.id,
        size_bytes=st.st_size,
        mtime=entry.last_observed_mtime or now,
        sha256=sha256,
        content_object_id=None,
        extraction_status=status.value,
        error_code=error_code,
        error_message=error_message,
    )
    session.add(version)
    await session.flush()
    entry.current_file_version_id = version.id
    entry.state = entry_state.value
    if status is ExtractionStatus.unsupported:
        await ctx.engine.complete(
            session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
        )
    await session.commit()
