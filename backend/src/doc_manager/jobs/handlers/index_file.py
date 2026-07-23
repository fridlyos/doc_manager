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
import uuid
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.artifact_store import ArtifactStore
from doc_manager.chunking import Chunk as ChunkRecord
from doc_manager.chunking import (
    ChunkingProfile,
    chunk_id,
    chunk_pages,
    default_chunking_profile,
)
from doc_manager.core.config import Settings, get_settings
from doc_manager.core.hashing import sha256_file
from doc_manager.core.logging import get_logger
from doc_manager.db.models import (
    CatalogEntry,
    Chunk,
    ContentObject,
    FileVersion,
    IngestionJob,
    SourceLocation,
)
from doc_manager.db.session import db_now
from doc_manager.domain.enums import CatalogEntryState, ExtractionStatus, JobStatus
from doc_manager.embedding import EmbeddingProfile, build_embedding_service
from doc_manager.extraction import ExtractionError, Extractor, get_extractor
from doc_manager.extraction.normalize import NormalizedDocument, normalize
from doc_manager.extraction.profile import extraction_profile_hash
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import LeaseLostError, PermanentJobError, TransientJobError
from doc_manager.vectors import QdrantRepository, build_point, build_qdrant_repository

log = get_logger("doc_manager.jobs.index_file")


def _chunking_profile(settings: Settings) -> ChunkingProfile:
    return default_chunking_profile(
        target_tokens=settings.chunk_target_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )


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

    settings = get_settings()
    normalized = normalize(extracted)
    profile_hash = extraction_profile_hash(extractor.name, extractor.version)
    store = ArtifactStore(settings.artifact_root)
    artifact = await asyncio.to_thread(
        store.store,
        normalized,
        extractor_name=extractor.name,
        extractor_version=extractor.version,
        extraction_profile_hash=profile_hash,
        metadata=extracted.metadata,
    )
    ctx.check_boundary()

    # Chunk (Phase 4.a) and, unless this content is already fully indexed under the
    # active profiles, embed (4.b) — both outside the fenced publish transaction so
    # the expensive embed does not hold the job lock. ensure_collection + upsert +
    # chunk rows happen in the fenced transaction so a stale worker cannot publish.
    chunking_profile = _chunking_profile(settings)
    chunks = chunk_pages(normalized.pages, profile=chunking_profile)
    embedding_service = build_embedding_service(settings)
    embedding_profile = embedding_service.profile
    qdrant = build_qdrant_repository(settings, embedding_profile)

    vectors = None
    if chunks and not await _already_indexed(
        ctx.session, normalized, profile_hash, chunking_profile, embedding_profile, len(chunks)
    ):
        await qdrant.ensure_collection(embedding_profile)
        vectors = await asyncio.to_thread(
            embedding_service.embed_documents, [c.text for c in chunks]
        )
    ctx.check_boundary()

    await _publish_success(
        ctx,
        entry,
        st,
        current_sha,
        normalized,
        extractor,
        profile_hash,
        artifact.relative_path,
        chunks=chunks,
        vectors=vectors,
        chunking_profile=chunking_profile,
        embedding_profile=embedding_profile,
        qdrant=qdrant,
    )
    log.info(
        "file_indexed",
        job_id=str(job.id),
        catalog_entry_id=str(entry.id),
        pages=len(normalized.pages),
        chunks=len(chunks),
        embedded=vectors is not None,
        artifact_reused=artifact.reused,
    )


async def _already_indexed(
    session: AsyncSession,
    normalized: NormalizedDocument,
    extraction_profile_hash_: str,
    chunking_profile: ChunkingProfile,
    embedding_profile: EmbeddingProfile,
    chunk_count: int,
) -> bool:
    """True when a content object with the full chunk set already exists.

    Lets a duplicate file (identical structured content) reuse the existing
    chunks/points without re-embedding.
    """
    content = await session.scalar(
        select(ContentObject).where(
            ContentObject.structure_hash == normalized.structure_hash,
            ContentObject.extraction_profile_hash == extraction_profile_hash_,
            ContentObject.normalization_version == normalized.normalization_version,
        )
    )
    if content is None:
        return False
    have = await session.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(
            Chunk.content_object_id == content.id,
            Chunk.chunking_profile_hash == chunking_profile.hash,
            Chunk.embedding_profile_hash == embedding_profile.hash,
        )
    )
    return bool(have == chunk_count)


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
    *,
    chunks: list[ChunkRecord],
    vectors: list[list[float]] | None,
    chunking_profile: ChunkingProfile,
    embedding_profile: EmbeddingProfile,
    qdrant: QdrantRepository,
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

    # Persist chunk rows + upsert vector points when we (re-)embedded. Both are
    # idempotent on the deterministic chunk id, so a retry never duplicates.
    if vectors is not None:
        await _index_chunks(
            session, content.id, chunks, vectors, chunking_profile, embedding_profile, qdrant
        )

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


async def _index_chunks(
    session: AsyncSession,
    content_object_id: uuid.UUID,
    chunks: list[ChunkRecord],
    vectors: list[list[float]],
    chunking_profile: ChunkingProfile,
    embedding_profile: EmbeddingProfile,
    qdrant: QdrantRepository,
) -> None:
    """Upsert vector points and persist chunk rows for one content object.

    Point/chunk ids are deterministic (content + profiles + index), so the Qdrant
    upsert and the ``ON CONFLICT`` row upsert are both idempotent. Points are
    written before the transaction commits so a committed ``indexed`` state always
    implies searchable vectors.
    """
    cp_hash = chunking_profile.hash
    ep_hash = embedding_profile.hash
    points = []
    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        cid = chunk_id(content_object_id, cp_hash, chunk.index)
        points.append(
            build_point(
                content_object_id=content_object_id,
                chunk_id=cid,
                chunk=chunk,
                vector=vector,
                chunking_profile_hash=cp_hash,
                embedding_profile_hash=ep_hash,
            )
        )
        rows.append(
            {
                "id": cid,
                "content_object_id": content_object_id,
                "chunk_index": chunk.index,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "token_count": chunk.token_count,
                "text_hash": chunk.text_hash,
                "chunking_profile_hash": cp_hash,
                "embedding_profile_hash": ep_hash,
            }
        )
    await qdrant.upsert_points(points)
    stmt = pg_insert(Chunk).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Chunk.id],
        set_={
            "embedding_profile_hash": stmt.excluded.embedding_profile_hash,
            "token_count": stmt.excluded.token_count,
            "page_start": stmt.excluded.page_start,
            "page_end": stmt.excluded.page_end,
            "text_hash": stmt.excluded.text_hash,
            "updated_at": func.clock_timestamp(),
        },
    )
    await session.execute(stmt)


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
