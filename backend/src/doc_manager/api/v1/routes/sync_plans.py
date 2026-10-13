"""Sync plan routes (TECHSTACK 5.14, §8; Phase 7.c).

``POST /sync-plans`` creates an immutable, read-only dry-run comparison of a source
location against a target and enqueues ``build_sync_plan`` to fill it. There is
intentionally **no execution endpoint** — the MVP compares and plans only. Paths
are server-resolved display / relative paths; no route reads a filesystem path.

A plan can be rebuilt in place (``POST /sync-plans/{id}/rebuild``: items cleared,
re-compared against the current catalog) or deleted (``DELETE``). Both refuse with
409 while a build job for the plan is still open.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
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
)
from doc_manager.api.serializers import serialize_sync_plan, serialize_sync_plan_item
from doc_manager.core.config import Settings
from doc_manager.db.models import IngestionJob, SourceLocation, SyncPlan, SyncPlanItem
from doc_manager.domain.enums import OPEN_STATUSES, JobOrigin, JobType, SyncPlanStatus
from doc_manager.jobs.queue import JobEngine

router = APIRouter(prefix="/sync-plans", tags=["sync-plans"])

_ROUTE = "/api/v1/sync-plans"
_ITEMS_ROUTE = "/api/v1/sync-plans/{id}/items"
_ACTIONS = {"already_present", "copy", "conflict", "manual_review"}


class SyncPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_location_id: uuid.UUID
    target_location_id: uuid.UUID


async def _load_location(session: AsyncSession, location_id: uuid.UUID) -> SourceLocation:
    location = await session.get(SourceLocation, location_id)
    if location is None:
        raise Problem(404, "not_found", "No such source location.")
    return location


async def _load_plan(session: AsyncSession, plan_id: str) -> SyncPlan:
    plan = await session.get(SyncPlan, parse_uuid(plan_id, what="sync plan"))
    if plan is None:
        raise Problem(404, "not_found", "No such sync plan.")
    return plan


async def _require_no_open_build(session: AsyncSession, plan: SyncPlan) -> None:
    open_jobs = await session.scalar(
        select(func.count())
        .select_from(IngestionJob)
        .where(
            IngestionJob.job_type == JobType.build_sync_plan.value,
            IngestionJob.status.in_([s.value for s in OPEN_STATUSES]),
            IngestionJob.payload_json["plan_id"].astext == str(plan.id),
        )
    )
    if open_jobs:
        raise Problem(
            409,
            "conflict",
            "This sync plan is still building; wait for it or cancel its job first.",
        )


def _engine(settings: Settings) -> JobEngine:
    return JobEngine(
        base_delay_seconds=settings.job_retry_base_delay_seconds,
        max_delay_seconds=settings.job_retry_max_delay_seconds,
    )


@router.post("", status_code=202)
async def create_sync_plan(
    request: Request,
    response: Response,
    body: SyncPlanCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> dict[str, Any]:
    if body.source_location_id == body.target_location_id:
        raise Problem(422, "validation_failed", "source and target must differ.")
    await _load_location(session, body.source_location_id)
    await _load_location(session, body.target_location_id)

    engine = _engine(settings)
    fingerprint = request_fingerprint(
        {"source": str(body.source_location_id), "target": str(body.target_location_id)}, None
    )
    outcome = await reserve_idempotency(
        session,
        method="POST",
        route_template="/api/v1/sync-plans",
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if outcome.replayed_job is not None:
        plan_id = (outcome.replayed_job.payload_json or {}).get("plan_id")
        plan = await session.get(SyncPlan, uuid.UUID(str(plan_id))) if plan_id else None
        if plan is None:
            raise Problem(404, "not_found", "The original sync plan no longer exists.")
        replayed = True
    else:
        plan = SyncPlan(
            source_location_id=body.source_location_id,
            target_location_id=body.target_location_id,
            status=SyncPlanStatus.building.value,
        )
        session.add(plan)
        await session.flush()
        job, _ = await engine.enqueue(
            session,
            job_type=JobType.build_sync_plan,
            payload={"version": 1, "plan_id": str(plan.id)},
            origin=JobOrigin.api,
            max_attempts=settings.job_max_attempts,
            request_key=idempotency_key,
            actor="api",
        )
        assert outcome.record is not None
        outcome.record.job_id = job.id
        replayed = False
    await session.commit()
    response.headers["Location"] = f"/api/v1/sync-plans/{plan.id}"
    response.headers["Retry-After"] = "2"
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return envelope(request, serialize_sync_plan(plan), idempotency_replayed=replayed)


@router.get("")
async def list_sync_plans(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    limit = parse_limit(request)
    stmt = select(SyncPlan)
    cursor = request.query_params.get("cursor")
    if cursor:
        raw_value, last_id = decode_cursor(
            settings, cursor, route=_ROUTE, sort="-created_at", filters={}
        )
        try:
            last_at = datetime.fromisoformat(raw_value)
        except ValueError as exc:
            raise Problem(400, "invalid_cursor", "Cursor not valid here.") from exc
        stmt = stmt.where(
            (SyncPlan.created_at < last_at)
            | ((SyncPlan.created_at == last_at) & (SyncPlan.id > uuid.UUID(last_id)))
        )
    ordered = stmt.order_by(SyncPlan.created_at.desc(), SyncPlan.id.asc())
    rows = (await session.scalars(ordered.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(
            settings,
            route=_ROUTE,
            sort="-created_at",
            filters={},
            last_key=[last.created_at.isoformat(), str(last.id)],
        )
    return collection_envelope(
        request,
        [serialize_sync_plan(p) for p in rows],
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
        effective_sort=["-created_at", "id"],
    )


@router.get("/{plan_id}")
async def get_sync_plan(
    request: Request,
    plan_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    plan = await _load_plan(session, plan_id)
    return envelope(request, serialize_sync_plan(plan))


@router.post("/{plan_id}/rebuild", status_code=202)
async def rebuild_sync_plan(
    request: Request,
    response: Response,
    plan_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> dict[str, Any]:
    """Re-run the comparison against the current catalog, replacing the items.

    Also the way out for a plan stuck in ``building`` after its job failed.
    """
    plan = await _load_plan(session, plan_id)
    outcome = await reserve_idempotency(
        session,
        method="POST",
        route_template="/api/v1/sync-plans/{plan_id}/rebuild",
        key=idempotency_key,
        fingerprint=request_fingerprint({"plan_id": str(plan.id)}, None),
    )
    replayed = outcome.replayed_job is not None
    if not replayed:
        await _require_no_open_build(session, plan)
        await session.execute(delete(SyncPlanItem).where(SyncPlanItem.plan_id == plan.id))
        plan.status = SyncPlanStatus.building.value
        plan.item_count = 0
        plan.covered_percent = 0.0
        plan.summary_json = None
        plan.error_code = None
        plan.built_at = None
        job, _ = await _engine(settings).enqueue(
            session,
            job_type=JobType.build_sync_plan,
            payload={"version": 1, "plan_id": str(plan.id)},
            origin=JobOrigin.api,
            max_attempts=settings.job_max_attempts,
            request_key=idempotency_key,
            actor="api",
        )
        assert outcome.record is not None
        outcome.record.job_id = job.id
    await session.commit()
    await session.refresh(plan)
    response.headers["Location"] = f"/api/v1/sync-plans/{plan.id}"
    response.headers["Retry-After"] = "2"
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return envelope(request, serialize_sync_plan(plan), idempotency_replayed=replayed)


@router.delete("/{plan_id}", status_code=204)
async def delete_sync_plan(
    plan_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Remove a plan and its items (FK cascade). Source files are never touched."""
    plan = await _load_plan(session, plan_id)
    await _require_no_open_build(session, plan)
    await session.delete(plan)
    await session.commit()
    return Response(status_code=204)


@router.get("/{plan_id}/items")
async def list_sync_plan_items(
    request: Request,
    plan_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    plan_uuid = parse_uuid(plan_id, what="sync plan")
    if await session.get(SyncPlan, plan_uuid) is None:
        raise Problem(404, "not_found", "No such sync plan.")
    limit = parse_limit(request)
    filters = parse_bracket_filters(request, allowed={"action": _ACTIONS})
    stmt = select(SyncPlanItem).where(SyncPlanItem.plan_id == plan_uuid)
    if "action" in filters:
        stmt = stmt.where(SyncPlanItem.action.in_(filters["action"]))
    route = _ITEMS_ROUTE
    cursor = request.query_params.get("cursor")
    if cursor:
        _, last_id = decode_cursor(settings, cursor, route=route, sort="id", filters=filters)
        stmt = stmt.where(SyncPlanItem.id > uuid.UUID(last_id))
    ordered = stmt.order_by(SyncPlanItem.id.asc())
    rows = (await session.scalars(ordered.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        next_cursor = encode_cursor(
            settings,
            route=route,
            sort="id",
            filters=filters,
            last_key=[str(rows[-1].id), str(rows[-1].id)],
        )
    return collection_envelope(
        request,
        [serialize_sync_plan_item(i) for i in rows],
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
        effective_sort=["id"],
    )
