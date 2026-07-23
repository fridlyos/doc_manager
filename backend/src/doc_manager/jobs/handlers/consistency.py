"""`catalog_consistency_check` handler (TECHSTACK 5.9, Phase 4.e).

Compares the SQL ``chunks`` rows against the Qdrant points for the active
embedding profile and reports drift — chunks whose vector point is missing, and
points with no backing chunk row. Report-only: repair (re-index missing,
tombstone orphans) is left to ``index_file`` / ``remove_stale_vectors`` so this
job never mutates the store. The result is emitted as a structured log and
recorded on the job's progress counters.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.core.config import get_settings
from doc_manager.core.logging import get_logger
from doc_manager.db.models import Chunk
from doc_manager.embedding import resolve_embedding_profile
from doc_manager.jobs.context import JobContext
from doc_manager.vectors import build_qdrant_repository, point_id
from doc_manager.vectors.repository import QdrantRepository

log = get_logger("doc_manager.jobs.consistency")


@dataclass(slots=True)
class ConsistencyReport:
    """Aggregate SQL↔vector drift for one embedding profile."""

    content_objects_checked: int = 0
    chunks_expected: int = 0
    points_found: int = 0
    missing_points: int = 0
    orphan_points: int = 0
    drifted_content_ids: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.missing_points == 0 and self.orphan_points == 0

    def account(self, content_id: uuid.UUID, expected: set[str], actual: set[str]) -> None:
        self.content_objects_checked += 1
        self.chunks_expected += len(expected)
        self.points_found += len(actual)
        missing = expected - actual
        orphan = actual - expected
        self.missing_points += len(missing)
        self.orphan_points += len(orphan)
        if missing or orphan:
            self.drifted_content_ids.append(str(content_id))


async def handle_catalog_consistency_check(ctx: JobContext) -> None:
    session = ctx.session
    settings = get_settings()
    profile = resolve_embedding_profile(settings)  # no model load — registry lookup only.
    repo = build_qdrant_repository(settings, profile)
    report = await scan_consistency(session, repo, profile.hash)

    log.info(
        "catalog_consistency_check",
        job_id=str(ctx.job.id),
        embedding_profile=profile.hash[:12],
        clean=report.clean,
        content_objects_checked=report.content_objects_checked,
        chunks_expected=report.chunks_expected,
        points_found=report.points_found,
        missing_points=report.missing_points,
        orphan_points=report.orphan_points,
        drifted_content_objects=len(report.drifted_content_ids),
    )
    await ctx.engine.complete(
        session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
    )
    await session.commit()


async def scan_consistency(
    session: AsyncSession, repo: QdrantRepository, embedding_profile_hash: str
) -> ConsistencyReport:
    """Compare chunk rows to vector points for one embedding profile."""
    report = ConsistencyReport()
    content_ids = (
        await session.scalars(
            select(Chunk.content_object_id)
            .where(Chunk.embedding_profile_hash == embedding_profile_hash)
            .distinct()
        )
    ).all()
    for content_id in content_ids:
        rows = (
            await session.scalars(
                select(Chunk).where(
                    Chunk.content_object_id == content_id,
                    Chunk.embedding_profile_hash == embedding_profile_hash,
                )
            )
        ).all()
        expected = {
            str(
                point_id(
                    content_id,
                    row.chunking_profile_hash,
                    row.embedding_profile_hash,
                    row.chunk_index,
                )
            )
            for row in rows
        }
        actual = await repo.point_ids_for_content(
            content_id, embedding_profile_hash=embedding_profile_hash
        )
        report.account(content_id, expected, actual)
    return report
