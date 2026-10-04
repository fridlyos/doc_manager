"""Job resource routes: list, get, cancel, manual retry (contract secs. 6-7)."""

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
from doc_manager.api.serializers import serialize_job
from doc_manager.core.config import Settings
from doc_manager.db.models import IngestionJob
from doc_manager.domain.enums import JobStatus, JobType
from doc_manager.jobs.queue import (
    JobEngine,
    NotCancellableError,
    NotRetryableError,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

_ROUTE = "/api/v1/jobs"
_FILTERS: dict[str, set[str] | None] = {
    "status": {s.value for s in JobStatus},
    "job_type": {t.value for t in JobType},
    # Arbitrary UUID values (validated when applied) — lets the UI find a
    # location's current/last scan (Phase 9).
    "source_location_id": None,
}

#: Index-file child statuses the scan-progress view rolls up as "still working".
_INDEX_REMAINING = {
    JobStatus.queued.value,
    JobStatus.running.value,
    JobStatus.retry_wait.value,
}


async def _load_job(session: AsyncSession, job_id: str) -> IngestionJob:
    job = await session.get(IngestionJob, parse_uuid(job_id, what="job"))
    if job is None:
        raise Problem(404, "not_found", "No such job.")
    return job


async def _scan_summary(session: AsyncSession, job: IngestionJob) -> dict[str, Any]:
    """Merge the scan's persisted breakdown with a live aggregate of its
    ``index_file`` children (linked by ``root_job_id``) for the progress view."""
    rows = await session.execute(
        select(IngestionJob.status, func.count())
        .where(
            IngestionJob.root_job_id == job.id,
            IngestionJob.job_type == JobType.index_file.value,
        )
        .group_by(IngestionJob.status)
    )
    by_status: dict[str, int] = {row[0]: row[1] for row in rows.all()}
    summary: dict[str, Any] = dict(job.progress_detail_json or {})
    summary["indexed"] = by_status.get(JobStatus.succeeded.value, 0)
    summary["index_failed"] = by_status.get(JobStatus.failed.value, 0)
    summary["index_remaining"] = sum(by_status.get(s, 0) for s in _INDEX_REMAINING)
    summary["index_total"] = sum(by_status.values())
    return summary


@router.get("")
async def list_jobs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    limit = parse_limit(request)
    sort = parse_sort(
        request,
        allowed={"-requested_at": "", "requested_at": ""},
        default="-requested_at",
    )
    filters = parse_bracket_filters(request, allowed=_FILTERS)
    descending = sort.startswith("-")
    stmt = select(IngestionJob)
    # Values within one filter field are ORed; fields are ANDed (contract 5.3).
    if "status" in filters:
        stmt = stmt.where(IngestionJob.status.in_(filters["status"]))
    if "job_type" in filters:
        stmt = stmt.where(IngestionJob.job_type.in_(filters["job_type"]))
    if "source_location_id" in filters:
        try:
            location_ids = [uuid.UUID(v) for v in filters["source_location_id"]]
        except ValueError as exc:
            raise Problem(
                422, "validation_failed", "filter[source_location_id] must be a UUID."
            ) from exc
        stmt = stmt.where(IngestionJob.source_location_id.in_(location_ids))
    cursor = request.query_params.get("cursor")
    if cursor:
        raw_value, last_id = decode_cursor(
            settings, cursor, route=_ROUTE, sort=sort, filters=filters
        )
        try:
            last_at = datetime.fromisoformat(raw_value)
        except ValueError as exc:
            raise Problem(
                400, "invalid_cursor", "The pagination cursor is not valid here."
            ) from exc
        column = IngestionJob.requested_at
        if descending:
            stmt = stmt.where(
                (column < last_at) | ((column == last_at) & (IngestionJob.id > uuid.UUID(last_id)))
            )
        else:
            stmt = stmt.where(
                (column > last_at) | ((column == last_at) & (IngestionJob.id > uuid.UUID(last_id)))
            )
    order = IngestionJob.requested_at.desc() if descending else IngestionJob.requested_at.asc()
    rows = (
        await session.scalars(stmt.order_by(order, IngestionJob.id.asc()).limit(limit + 1))
    ).all()
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
            last_key=[last.requested_at.isoformat(), str(last.id)],
        )
    return collection_envelope(
        request,
        [serialize_job(row) for row in rows],
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
        effective_sort=[sort, "id"],
    )


@router.get("/{job_id}")
async def get_job(
    request: Request,
    job_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    job = await _load_job(session, job_id)
    data = serialize_job(job)
    if job.job_type == JobType.scan_location.value:
        data["scan_summary"] = await _scan_summary(session, job)
    return envelope(request, data)


@router.post("/{job_id}/cancel")
async def cancel_job(
    request: Request,
    job_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    engine = JobEngine(
        base_delay_seconds=settings.job_retry_base_delay_seconds,
        max_delay_seconds=settings.job_retry_max_delay_seconds,
    )
    try:
        job = await engine.request_cancel(session, parse_uuid(job_id, what="job"), actor="api")
    except LookupError as exc:
        raise Problem(404, "not_found", "No such job.") from exc
    except NotCancellableError as exc:
        raise Problem(
            409,
            "job_not_cancellable",
            f"Job is already terminal ({exc.status}).",
        ) from exc
    await session.commit()
    return envelope(request, serialize_job(job))


@router.post("/{job_id}/retry", status_code=202)
async def retry_job(
    request: Request,
    response: Response,
    job_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> dict[str, Any]:
    engine = JobEngine(
        base_delay_seconds=settings.job_retry_base_delay_seconds,
        max_delay_seconds=settings.job_retry_max_delay_seconds,
    )
    fingerprint = request_fingerprint({"job_id": job_id}, None)
    source = await _load_job(session, job_id)
    outcome = await reserve_idempotency(
        session,
        method="POST",
        route_template="/api/v1/jobs/{job_id}/retry",
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if outcome.replayed_job is not None:
        job, coalesced, replayed = outcome.replayed_job, False, True
    else:
        try:
            job, coalesced = await engine.create_manual_retry(
                session, source, actor="api", request_key=idempotency_key
            )
        except NotRetryableError as exc:
            raise Problem(
                409,
                "job_not_retryable",
                f"Only failed or cancelled jobs can be retried (status: {exc.status}).",
            ) from exc
        replayed = False
        assert outcome.record is not None
        outcome.record.job_id = job.id
    await session.commit()
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    response.headers["Retry-After"] = "2"
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return envelope(request, serialize_job(job), idempotency_replayed=replayed, coalesced=coalesced)
