"""Document (catalog entry) resource routes (API contract secs. 5-6, 8).

A "document" is the public projection of a ``catalog_entries`` row joined to its
current ``file_versions`` and ``content_objects``. These routes surface the
per-document extraction outcome — including isolated extraction errors — and
allow a manual re-index that enqueues a durable ``index_file`` job.

``GET /errors`` is the error queue: the same projection filtered to documents
whose latest indexing attempt failed, so a user can find and retry them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from doc_manager.api.dependencies import (
    get_session,
    get_settings_dep,
    parse_uuid,
    request_fingerprint,
    require_idempotency_key,
    reserve_idempotency,
)
from doc_manager.api.envelope import collection_envelope, envelope
from doc_manager.api.errors import Problem
from doc_manager.api.pagination import (
    decode_cursor,
    encode_cursor,
    parse_bracket_filters,
    parse_limit,
    parse_sort,
)
from doc_manager.api.serializers import serialize_document, serialize_job
from doc_manager.core.config import Settings
from doc_manager.db.models import (
    CatalogEntry,
    ContentObject,
    FileVersion,
    SourceLocation,
)
from doc_manager.domain.enums import (
    CatalogEntryState,
    ExtractionStatus,
    JobOrigin,
    JobType,
)
from doc_manager.jobs.queue import JobEngine

router = APIRouter(prefix="/documents", tags=["documents"])

_ROUTE = "/api/v1/documents"
_SORTS = {
    "-updated_at": (CatalogEntry.updated_at, True),
    "updated_at": (CatalogEntry.updated_at, False),
    "-created_at": (CatalogEntry.created_at, True),
    "created_at": (CatalogEntry.created_at, False),
    "file_name": (CatalogEntry.file_name, False),
    "-file_name": (CatalogEntry.file_name, True),
}
_FILTERS: dict[str, set[str] | None] = {
    "state": {s.value for s in CatalogEntryState},
    "extension": None,
    "source_location_id": None,
}


def _select_documents() -> Any:
    """Base SELECT joining an entry to its current version, content, and location.

    ``current_file_version_id`` may be NULL (never indexed) so both joins are
    outer; the location join is inner because an entry cannot outlive its
    location (FK cascade).
    """
    version = aliased(FileVersion)
    content = aliased(ContentObject)
    stmt = (
        select(CatalogEntry, SourceLocation, version, content)
        .join(SourceLocation, SourceLocation.id == CatalogEntry.source_location_id)
        .join(version, version.id == CatalogEntry.current_file_version_id, isouter=True)
        .join(content, content.id == version.content_object_id, isouter=True)
    )
    return stmt, version, content


async def _load_document(
    session: AsyncSession, document_id: str
) -> tuple[CatalogEntry, SourceLocation, FileVersion | None, ContentObject | None]:
    entry_id = parse_uuid(document_id, what="document")
    stmt, _version, _content = _select_documents()
    row = (await session.execute(stmt.where(CatalogEntry.id == entry_id))).first()
    if row is None:
        raise Problem(404, "not_found", "No such document.")
    entry, location, version, content = row
    return entry, location, version, content


@router.get("")
async def list_documents(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    limit = parse_limit(request)
    sort = parse_sort(request, allowed=dict.fromkeys(_SORTS, ""), default="-updated_at")
    filters = parse_bracket_filters(request, allowed=_FILTERS)
    column, descending = _SORTS[sort]

    stmt, _version, _content = _select_documents()
    # Values within one filter field are ORed; fields are ANDed (contract 5.3).
    if "state" in filters:
        stmt = stmt.where(CatalogEntry.state.in_(filters["state"]))
    if "extension" in filters:
        stmt = stmt.where(CatalogEntry.extension.in_(filters["extension"]))
    if "source_location_id" in filters:
        try:
            location_ids = [uuid.UUID(v) for v in filters["source_location_id"]]
        except ValueError as exc:
            raise Problem(
                422, "validation_failed", "invalid value for filter[source_location_id]."
            ) from exc
        stmt = stmt.where(CatalogEntry.source_location_id.in_(location_ids))

    cursor = request.query_params.get("cursor")
    if cursor:
        raw_value, last_id = decode_cursor(
            settings, cursor, route=_ROUTE, sort=sort, filters=filters
        )
        last_value: Any = raw_value
        if column.key in ("updated_at", "created_at"):
            try:
                last_value = datetime.fromisoformat(raw_value)
            except ValueError as exc:
                raise Problem(
                    400, "invalid_cursor", "The pagination cursor is not valid here."
                ) from exc
        if descending:
            stmt = stmt.where(
                (column < last_value)
                | ((column == last_value) & (CatalogEntry.id > uuid.UUID(last_id)))
            )
        else:
            stmt = stmt.where(
                (column > last_value)
                | ((column == last_value) & (CatalogEntry.id > uuid.UUID(last_id)))
            )
    ordered = stmt.order_by(column.desc() if descending else column.asc(), CatalogEntry.id.asc())
    rows = (await session.execute(ordered.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        entry = rows[-1][0]
        raw_value = getattr(entry, column.key)
        next_cursor = encode_cursor(
            settings,
            route=_ROUTE,
            sort=sort,
            filters=filters,
            last_key=[str(raw_value), str(entry.id)],
        )
    return collection_envelope(
        request,
        [serialize_document(e, loc, ver, cont) for e, loc, ver, cont in rows],
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
        effective_sort=[sort, "id"],
    )


@router.get("/{document_id}")
async def get_document(
    request: Request,
    document_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    entry, location, version, content = await _load_document(session, document_id)
    return envelope(request, serialize_document(entry, location, version, content))


@router.post("/{document_id}/reindex", status_code=202)
async def reindex_document(
    request: Request,
    response: Response,
    document_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> dict[str, Any]:
    """Re-run extraction for one document by enqueuing an ``index_file`` job.

    Shares the scanner's ``index:{entry_id}`` dedupe key, so a manual reindex
    coalesces onto an in-flight indexing job for the same entry rather than
    racing it. An entry that has never been observed (no ``sha256``) cannot be
    indexed yet — that requires a location scan first.
    """
    engine = JobEngine(
        base_delay_seconds=settings.job_retry_base_delay_seconds,
        max_delay_seconds=settings.job_retry_max_delay_seconds,
    )
    fingerprint = request_fingerprint({"document_id": document_id}, None)
    entry, _location, _version, _content = await _load_document(session, document_id)
    if entry.sha256 is None:
        raise Problem(
            409,
            "conflict",
            "This document has no observed content yet; scan its location first.",
        )
    outcome = await reserve_idempotency(
        session,
        method="POST",
        route_template="/api/v1/documents/{document_id}/reindex",
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if outcome.replayed_job is not None:
        job, coalesced, replayed = outcome.replayed_job, False, True
    else:
        job, coalesced = await engine.enqueue(
            session,
            job_type=JobType.index_file,
            payload={"version": 1, "catalog_entry_id": str(entry.id)},
            origin=JobOrigin.api,
            catalog_entry_id=entry.id,
            dedupe_key=f"index:{entry.id}",
            max_attempts=settings.job_max_attempts,
            request_key=idempotency_key,
            actor="api",
        )
        replayed = False
        assert outcome.record is not None
        outcome.record.job_id = job.id
    await session.commit()
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    response.headers["Retry-After"] = "2"
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return envelope(request, serialize_job(job), idempotency_replayed=replayed, coalesced=coalesced)


# The error queue is a documents projection, so it lives here rather than under a
# separate resource; it is mounted at /api/v1/errors by the router.
errors_router = APIRouter(prefix="/errors", tags=["errors"])

_ERRORS_ROUTE = "/api/v1/errors"


@errors_router.get("")
async def list_errors(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    """Documents whose latest indexing attempt failed, newest first.

    Errors are isolated per document (the failure lives on the file version, not
    the whole scan), so this queue is exactly the set a user can act on with a
    per-document reindex.
    """
    limit = parse_limit(request)
    stmt, version, _content = _select_documents()
    stmt = stmt.where(
        CatalogEntry.state == CatalogEntryState.failed.value,
        version.extraction_status == ExtractionStatus.failed.value,
    )
    cursor = request.query_params.get("cursor")
    if cursor:
        raw_value, last_id = decode_cursor(
            settings, cursor, route=_ERRORS_ROUTE, sort="-updated_at", filters={}
        )
        try:
            last_at = datetime.fromisoformat(raw_value)
        except ValueError as exc:
            raise Problem(
                400, "invalid_cursor", "The pagination cursor is not valid here."
            ) from exc
        stmt = stmt.where(
            (CatalogEntry.updated_at < last_at)
            | ((CatalogEntry.updated_at == last_at) & (CatalogEntry.id > uuid.UUID(last_id)))
        )
    ordered = stmt.order_by(CatalogEntry.updated_at.desc(), CatalogEntry.id.asc())
    rows = (await session.execute(ordered.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        entry = rows[-1][0]
        next_cursor = encode_cursor(
            settings,
            route=_ERRORS_ROUTE,
            sort="-updated_at",
            filters={},
            last_key=[entry.updated_at.isoformat(), str(entry.id)],
        )
    return collection_envelope(
        request,
        [serialize_document(e, loc, ver, cont) for e, loc, ver, cont in rows],
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
        effective_sort=["-updated_at", "id"],
    )
