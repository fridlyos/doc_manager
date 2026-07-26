"""`remove_stale_vectors` handler (TECHSTACK 5.9, §14 Phase 6.b).

Retires Qdrant collections superseded by an embedding-profile change. After a
profile-driven rebuild (re-index everything under the new profile — Phase 6.a
``/system/reindex``), the new embedding profile writes a **new** collection
(profile isolation, Phase 4.c) and the old collection is left holding stale
points. This job drops those old collections, keeping only the active profile's.

It is the retire half of the **controlled rebuild** (exit criterion 3): the new
collection exists before the old is dropped, and only same-namespace collections
are touched. Chunk *rows* self-heal — ``chunk_id`` is embedding-agnostic, so a
re-index upserts the row in place with the new ``embedding_profile_hash``. Orphan
content cleanup (deleted files, chunking-profile changes) is Phase 6.d.

Safe to re-run: dropping an already-absent collection is a no-op.
"""

from __future__ import annotations

from doc_manager.core.config import get_settings
from doc_manager.core.logging import get_logger
from doc_manager.embedding import resolve_embedding_profile
from doc_manager.jobs.context import JobContext
from doc_manager.vectors import build_qdrant_repository
from doc_manager.vectors.repository import QdrantRepository

log = get_logger("doc_manager.jobs.cleanup")


async def drop_stale_collections(
    repo: QdrantRepository, *, active: str, base_prefix: str
) -> list[str]:
    """Drop every collection in our namespace except the active one.

    Only names starting with ``base_prefix`` are considered, so an unrelated
    collection is never touched. Returns the names dropped.
    """
    dropped = []
    for name in await repo.list_collection_names():
        if name != active and name.startswith(base_prefix):
            await repo.drop_collection(name)
            dropped.append(name)
    return dropped


async def handle_remove_stale_vectors(ctx: JobContext) -> None:
    session = ctx.session
    settings = get_settings()
    profile = resolve_embedding_profile(settings)  # registry lookup only, no model load
    repo = build_qdrant_repository(settings, profile)

    dropped = await drop_stale_collections(
        repo, active=repo.collection, base_prefix=f"{settings.qdrant_collection}__"
    )

    log.info(
        "remove_stale_vectors",
        job_id=str(ctx.job.id),
        active_collection=repo.collection,
        dropped=len(dropped),
    )
    await ctx.engine.complete(
        session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
    )
    await session.commit()
