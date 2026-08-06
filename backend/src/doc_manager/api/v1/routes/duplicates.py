"""Duplicate + coverage routes (TECHSTACK 5.4, §8; Phase 6.c).

Reads the materialized duplicate report and per-location coverage. Paths are
server-resolved display paths only. ``POST /duplicates/rebuild`` enqueues the
durable ``build_duplicate_report`` job.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from doc_manager.api.serializers import serialize_duplicate_group, serialize_job
from doc_manager.core.config import Settings
from doc_manager.db.models import CatalogEntry, DuplicateGroup, DuplicateMember, SourceLocation
from doc_manager.domain.enums import JobOrigin, JobType
from doc_manager.jobs.queue import JobEngine

router = APIRouter(prefix="/duplicates", tags=["duplicates"])

_ROUTE = "/api/v1/duplicates"
_SORTS = {
    "-member_count": (DuplicateGroup.member_count, True),
    "member_count": (DuplicateGroup.member_count, False),
    "-built_at": (DuplicateGroup.built_at, True),
    "built_at": (DuplicateGroup.built_at, False),
}
_FILTERS: dict[str, set[str] | None] = {"kind": {"exact", "text"}}


@router.get("")
async def list_duplicates(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    limit = parse_limit(request)
    sort = parse_sort(request, allowed=dict.fromkeys(_SORTS, ""), default="-member_count")
    filters = parse_bracket_filters(request, allowed=_FILTERS)
    column, descending = _SORTS[sort]

    stmt = select(DuplicateGroup)
    if "kind" in filters:
        stmt = stmt.where(DuplicateGroup.kind.in_(filters["kind"]))

    cursor = request.query_params.get("cursor")
    if cursor:
        raw_value, last_id = decode_cursor(
            settings, cursor, route=_ROUTE, sort=sort, filters=filters
        )
        last_value: Any = raw_value
        if column.key == "built_at":
            try:
                last_value = datetime.fromisoformat(raw_value)
            except ValueError as exc:
                raise Problem(400, "invalid_cursor", "Cursor not valid here.") from exc
        else:
            last_value = int(raw_value)
        tiebreak = DuplicateGroup.id > uuid.UUID(last_id)
        stmt = stmt.where(
            (column < last_value) | ((column == last_value) & tiebreak)
            if descending
            else (column > last_value) | ((column == last_value) & tiebreak)
        )
    ordered = stmt.order_by(column.desc() if descending else column.asc(), DuplicateGroup.id.asc())
    rows = (await session.scalars(ordered.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(
            settings,
            route=_ROUTE,
            sort=sort,
            filters=filters,
            last_key=[str(getattr(last, column.key)), str(last.id)],
        )
    return collection_envelope(
        request,
        [serialize_duplicate_group(g) for g in rows],
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
        effective_sort=[sort, "id"],
    )


@router.get("/{group_id}")
async def get_duplicate_group(
    request: Request,
    group_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    group = await session.get(DuplicateGroup, parse_uuid(group_id, what="duplicate group"))
    if group is None:
        raise Problem(404, "not_found", "No such duplicate group.")
    members = (
        await session.scalars(
            select(DuplicateMember)
            .where(DuplicateMember.group_id == group.id)
            .order_by(DuplicateMember.display_path.asc())
        )
    ).all()
    return envelope(request, serialize_duplicate_group(group, list(members)))


@router.post("/rebuild", status_code=202)
async def rebuild_duplicates(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> dict[str, Any]:
    engine = JobEngine(
        base_delay_seconds=settings.job_retry_base_delay_seconds,
        max_delay_seconds=settings.job_retry_max_delay_seconds,
    )
    fingerprint = request_fingerprint({"op": "build_duplicate_report"}, None)
    outcome = await reserve_idempotency(
        session,
        method="POST",
        route_template="/api/v1/duplicates/rebuild",
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if outcome.replayed_job is not None:
        job, coalesced, replayed = outcome.replayed_job, False, True
    else:
        job, coalesced = await engine.enqueue(
            session,
            job_type=JobType.build_duplicate_report,
            payload={"version": 1},
            origin=JobOrigin.api,
            dedupe_key="build_duplicate_report",
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


coverage_router = APIRouter(tags=["coverage"])


@coverage_router.get("/coverage")
async def get_coverage(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Per-source-location catalog coverage: entry counts by state."""
    counts = (
        await session.execute(
            select(
                CatalogEntry.source_location_id,
                CatalogEntry.state,
                func.count().label("n"),
            ).group_by(CatalogEntry.source_location_id, CatalogEntry.state)
        )
    ).all()
    by_location: dict[uuid.UUID, dict[str, int]] = {}
    for location_id, state, n in counts:
        by_location.setdefault(location_id, {})[state] = n

    locations = (await session.scalars(select(SourceLocation))).all()
    data = []
    for loc in locations:
        states = by_location.get(loc.id, {})
        data.append(
            {
                "source_location_id": str(loc.id),
                "name": loc.name,
                "total": sum(states.values()),
                "by_state": states,
            }
        )
    return envelope(request, data)
