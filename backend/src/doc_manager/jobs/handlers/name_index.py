"""`sync_name_index` handler: bring one location's file-name vectors up to date.

Enqueued after every scan (deduped per location). Compares the location's
**indexed** catalog entries with the name points in the name collection:

* indexed entry with no point, or whose name changed (moved/renamed) → embed + upsert;
* point whose entry is no longer indexed (failed, missing, deleted) → delete.

This is also the backfill: files indexed before the name index existed get their
name vectors on the next scan. ``index_file`` upserts a new file's name directly,
so this job only has to catch up, never lead.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from doc_manager.core.config import get_settings
from doc_manager.core.logging import get_logger
from doc_manager.db.models import CatalogEntry, FileVersion
from doc_manager.domain.enums import CatalogEntryState
from doc_manager.embedding import build_embedding_service
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import PermanentJobError
from doc_manager.vectors import NamePoint, build_name_index, file_name_text, name_hash

log = get_logger("doc_manager.jobs.name_index")

_EMBED_BATCH = 64


async def handle_sync_name_index(ctx: JobContext) -> None:
    session = ctx.session
    raw = (ctx.job.payload_json or {}).get("source_location_id")
    if raw is None:
        raise PermanentJobError("bad_request", "sync_name_index job has no source_location_id")
    location_id = uuid.UUID(str(raw))

    rows = (
        await session.execute(
            select(CatalogEntry.id, CatalogEntry.relative_path, FileVersion.content_object_id)
            .join(FileVersion, FileVersion.id == CatalogEntry.current_file_version_id)
            .where(
                CatalogEntry.source_location_id == location_id,
                CatalogEntry.state == CatalogEntryState.indexed.value,
                FileVersion.content_object_id.is_not(None),
            )
        )
    ).all()

    settings = get_settings()
    embedding = build_embedding_service(settings)
    names = build_name_index(settings, embedding.profile)
    await names.ensure_collection(embedding.profile)
    existing = await names.hashes_for_location(str(location_id))

    wanted: dict[str, tuple[str, str]] = {}  # entry id -> (name text, content id)
    for entry_id, relative_path, content_id in rows:
        text = file_name_text(relative_path)
        if text:
            wanted[str(entry_id)] = (text, str(content_id))

    stale = [eid for eid in existing if eid not in wanted]
    todo = [
        (eid, text, cid)
        for eid, (text, cid) in wanted.items()
        if existing.get(eid) != name_hash(text)
    ]
    for start in range(0, len(todo), _EMBED_BATCH):
        ctx.check_boundary()
        batch = todo[start : start + _EMBED_BATCH]
        vectors = await asyncio.to_thread(embedding.embed_documents, [t for _, t, _ in batch])
        await names.upsert(
            [
                NamePoint(
                    catalog_entry_id=eid,
                    content_object_id=cid,
                    source_location_id=str(location_id),
                    name_hash=name_hash(text),
                    vector=vector,
                )
                for (eid, text, cid), vector in zip(batch, vectors, strict=True)
            ]
        )
    await names.delete(stale)

    log.info(
        "sync_name_index",
        job_id=str(ctx.job.id),
        location_id=str(location_id),
        indexed=len(wanted),
        embedded=len(todo),
        removed=len(stale),
    )
    await ctx.engine.complete(
        session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
    )
    await session.commit()
