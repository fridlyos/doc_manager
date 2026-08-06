"""Bulk re-index fan-out (TECHSTACK 5.11, §14 Phase 6.a).

A parent job that enqueues an ``index_file`` per eligible catalog entry so a whole
location — or the whole catalog — is re-extracted/chunked/embedded under the
current profiles. Children are deduped on the scanner's ``index:{entry_id}`` key
(so a re-index coalesces with any in-flight indexing and repeated requests never
pile up) and share the parent's ``root_job_id`` for aggregate progress.

Re-indexing is idempotent: ``index_file`` re-verifies the fingerprint, reuses the
content object when structure + profiles match, and upserts vector points on
deterministic ids — so a re-index of unchanged content creates no duplicates.

The same handler serves the profile-driven rebuild in Phase 6.b (it adds the
stale-vector cleanup tail); here it covers the manual ``location`` and ``all``
scopes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from doc_manager.core.config import get_settings
from doc_manager.core.logging import get_logger
from doc_manager.db.models import CatalogEntry
from doc_manager.domain.enums import CatalogEntryState, JobOrigin, JobType
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import PermanentJobError

log = get_logger("doc_manager.jobs.reindex")

#: Entries worth re-indexing: they have observed content (a sha256) and are not
#: missing. ``discovered``/``queued`` already have indexing pending from a scan.
_REINDEXABLE = (
    CatalogEntryState.indexed.value,
    CatalogEntryState.failed.value,
    CatalogEntryState.unsupported.value,
)


async def handle_reindex_bulk(ctx: JobContext) -> None:
    session = ctx.session
    job = ctx.job
    payload = job.payload_json or {}
    scope = payload.get("scope")
    if scope not in ("location", "all"):
        raise PermanentJobError("bad_request", f"reindex scope must be location|all, got {scope!r}")

    stmt = select(CatalogEntry.id).where(
        CatalogEntry.sha256.is_not(None),
        CatalogEntry.state.in_(_REINDEXABLE),
    )
    if scope == "location":
        if job.source_location_id is None:
            raise PermanentJobError("bad_request", "location reindex has no source location")
        stmt = stmt.where(CatalogEntry.source_location_id == job.source_location_id)

    entry_ids = list((await session.scalars(stmt)).all())
    enqueued = await _fan_out(ctx, entry_ids)

    await ctx.report_progress(
        phase="reindex", current=len(entry_ids), total=len(entry_ids), unit="files"
    )
    log.info(
        "reindex_bulk",
        job_id=str(job.id),
        scope=scope,
        eligible=len(entry_ids),
        enqueued=enqueued,
    )
    await ctx.engine.complete(session, job, worker_id=ctx.worker_id, lease_token=ctx.lease_token)
    await session.commit()


async def _fan_out(ctx: JobContext, entry_ids: list[uuid.UUID]) -> int:
    """Enqueue a deduped index_file per entry under the parent's lineage."""
    max_attempts = get_settings().job_max_attempts
    root = ctx.job.root_job_id or ctx.job.id
    enqueued = 0
    for entry_id in entry_ids:
        _, coalesced = await ctx.engine.enqueue(
            ctx.session,
            job_type=JobType.index_file,
            payload={"version": 1, "catalog_entry_id": str(entry_id)},
            origin=JobOrigin.handler,
            catalog_entry_id=entry_id,
            dedupe_key=f"index:{entry_id}",
            root_job_id=root,
            max_attempts=max_attempts,
            actor="reindex",
        )
        if not coalesced:
            enqueued += 1
    return enqueued
