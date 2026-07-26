"""Maintenance routes (TECHSTACK §14 Phase 6). System-wide re-indexing.

``POST /api/v1/system/reindex`` enqueues a durable fan-out that re-indexes every
eligible document across all locations. Idempotency-Key'd like other job-creating
POSTs; the heavy work happens in the worker, not the request.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.api.dependencies import (
    get_session,
    get_settings_dep,
    request_fingerprint,
    require_idempotency_key,
    reserve_idempotency,
)
from doc_manager.api.envelope import envelope
from doc_manager.api.serializers import serialize_job
from doc_manager.core.config import Settings
from doc_manager.domain.enums import JobOrigin, JobType
from doc_manager.jobs.queue import JobEngine

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/reindex", status_code=202)
async def request_reindex_all(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> dict[str, Any]:
    """Re-index every eligible document across all locations (durable fan-out)."""
    engine = JobEngine(
        base_delay_seconds=settings.job_retry_base_delay_seconds,
        max_delay_seconds=settings.job_retry_max_delay_seconds,
    )
    fingerprint = request_fingerprint({"scope": "all"}, None)
    outcome = await reserve_idempotency(
        session,
        method="POST",
        route_template="/api/v1/system/reindex",
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if outcome.replayed_job is not None:
        job, coalesced, replayed = outcome.replayed_job, False, True
    else:
        job, coalesced = await engine.enqueue(
            session,
            job_type=JobType.reindex_all_for_profile,
            payload={"version": 1, "scope": "all"},
            origin=JobOrigin.api,
            dedupe_key="reindex:all",
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


@router.post("/remove-stale-vectors", status_code=202)
async def request_remove_stale_vectors(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> dict[str, Any]:
    """Retire Qdrant collections superseded by an embedding-profile change.

    The retire half of a controlled rebuild: run after ``POST /system/reindex``
    has rebuilt the corpus under the new profile.
    """
    engine = JobEngine(
        base_delay_seconds=settings.job_retry_base_delay_seconds,
        max_delay_seconds=settings.job_retry_max_delay_seconds,
    )
    fingerprint = request_fingerprint({"op": "remove_stale_vectors"}, None)
    outcome = await reserve_idempotency(
        session,
        method="POST",
        route_template="/api/v1/system/remove-stale-vectors",
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if outcome.replayed_job is not None:
        job, coalesced, replayed = outcome.replayed_job, False, True
    else:
        job, coalesced = await engine.enqueue(
            session,
            job_type=JobType.remove_stale_vectors,
            payload={"version": 1},
            origin=JobOrigin.api,
            dedupe_key="remove_stale_vectors",
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
