"""`build_sync_plan` handler (TECHSTACK 5.14, §14 Phase 7.c).

Loads the indexed catalog of the plan's source and target locations, compares them
with the pure ``sync`` library (by relative path, file hash, and normalized text
hash), and persists the classified items + coverage summary — a read-only dry run.
No filesystem access, no execution: the comparison uses catalog hashes only, so
source roots are never touched.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.core.display import display_path
from doc_manager.core.logging import get_logger
from doc_manager.db.models import (
    CatalogEntry,
    ContentObject,
    FileVersion,
    SourceLocation,
    SyncPlan,
    SyncPlanItem,
)
from doc_manager.db.session import db_now
from doc_manager.domain.enums import CatalogEntryState, SyncPlanStatus
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import PermanentJobError
from doc_manager.sync import EntryRow, LocationSnapshot, compare_locations

log = get_logger("doc_manager.jobs.sync_plan")


async def handle_build_sync_plan(ctx: JobContext) -> None:
    session = ctx.session
    plan_id = (ctx.job.payload_json or {}).get("plan_id")
    if plan_id is None:
        raise PermanentJobError("bad_request", "build_sync_plan job has no plan_id")
    plan = await session.get(SyncPlan, uuid.UUID(str(plan_id)))
    if plan is None:
        raise PermanentJobError("not_found", "sync plan no longer exists")

    source = await _snapshot(session, plan.source_location_id)
    target = await _snapshot(session, plan.target_location_id)
    result = compare_locations(source, target)

    for item in result.items:
        session.add(
            SyncPlanItem(
                plan_id=plan.id,
                action=item.action.value,
                reason=item.reason,
                source_relative_path=item.source_relative_path,
                source_sha256=item.source_sha256,
                source_text_hash=item.source_text_hash,
                target_relative_path=item.target_relative_path,
                target_sha256=item.target_sha256,
            )
        )

    cov = result.coverage
    plan.status = SyncPlanStatus.ready.value
    plan.item_count = len(result.items)
    plan.covered_percent = cov.covered_percent
    plan.summary_json = {
        "total_source": cov.total_source,
        "already_present": cov.already_present,
        "copy": cov.copy,
        "conflict": cov.conflict,
        "manual_review": cov.manual_review,
        "covered": cov.covered,
    }
    plan.built_at = await db_now(session)

    log.info(
        "build_sync_plan",
        job_id=str(ctx.job.id),
        plan_id=str(plan.id),
        items=plan.item_count,
        covered_percent=plan.covered_percent,
        conflicts=cov.conflict,
    )
    await ctx.engine.complete(
        session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
    )
    await session.commit()


async def _snapshot(session: AsyncSession, location_id: uuid.UUID) -> LocationSnapshot:
    rows = (
        await session.execute(
            select(
                CatalogEntry.relative_path,
                SourceLocation.path_style,
                SourceLocation.display_root,
                FileVersion.sha256,
                ContentObject.text_hash,
            )
            .join(FileVersion, CatalogEntry.current_file_version_id == FileVersion.id)
            .join(SourceLocation, SourceLocation.id == CatalogEntry.source_location_id)
            .join(ContentObject, FileVersion.content_object_id == ContentObject.id)
            .where(
                CatalogEntry.source_location_id == location_id,
                CatalogEntry.state == CatalogEntryState.indexed.value,
            )
        )
    ).all()
    return LocationSnapshot(
        entries=[
            EntryRow(
                relative_path=r[0],
                display_path=display_path(r[1], r[2], r[0]),
                sha256=r[3],
                text_hash=r[4],
            )
            for r in rows
        ]
    )
