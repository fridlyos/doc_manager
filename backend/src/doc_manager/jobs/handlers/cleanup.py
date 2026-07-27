"""`remove_stale_vectors` handler (TECHSTACK 5.9, §14 Phase 6.b + 6.d).

Retires vectors that no longer belong in the store, two ways:

* **Superseded collections (6.b)** — after a profile-driven rebuild (re-index
  under the new profile — ``/system/reindex``), the new embedding profile writes a
  **new** collection (profile isolation, Phase 4.c) and the old one is left with
  stale points. This drops those old, same-namespace collections, keeping only the
  active profile's. Chunk *rows* self-heal — ``chunk_id`` is embedding-agnostic, so
  a re-index upserts the row in place with the new ``embedding_profile_hash``.
* **Orphan content (6.d)** — a content object no longer referenced by any *indexed*
  catalog entry (its files were deleted, so every referencing entry is missing) has
  its points and chunk rows removed and the content object deleted. This is the
  delete-convergence half of exit criterion 1: a deleted file's vectors are retired.
  A later restore re-observes the path (``missing → discovered``) and re-indexes,
  which recreates the content object, chunks, and points by hash.

Safe to re-run: dropping an absent collection and deleting absent orphans are no-ops.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from doc_manager.core.config import get_settings
from doc_manager.core.logging import get_logger
from doc_manager.db.models import CatalogEntry, ContentObject, FileVersion
from doc_manager.domain.enums import CatalogEntryState
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


async def remove_orphan_content(ctx: JobContext, repo: QdrantRepository) -> int:
    """Delete content objects unreferenced by any indexed entry, with their vectors.

    Points are removed from the active collection (if present); the content object
    delete cascades its chunk rows. Returns the number of orphans removed.
    """
    session = ctx.session
    referenced = (
        select(FileVersion.content_object_id)
        .join(CatalogEntry, CatalogEntry.current_file_version_id == FileVersion.id)
        .where(
            CatalogEntry.state == CatalogEntryState.indexed.value,
            FileVersion.content_object_id.is_not(None),
        )
    )
    orphan_ids: list[uuid.UUID] = list(
        (
            await session.scalars(
                select(ContentObject.id).where(ContentObject.id.not_in(referenced))
            )
        ).all()
    )
    if not orphan_ids:
        return 0

    active_exists = repo.collection in await repo.list_collection_names()
    for content_id in orphan_ids:
        if active_exists:
            await repo.delete_for_content(content_id)  # remove points
    # Cascade drops the chunk rows with the content object.
    await session.execute(delete(ContentObject).where(ContentObject.id.in_(orphan_ids)))
    return len(orphan_ids)


async def handle_remove_stale_vectors(ctx: JobContext) -> None:
    session = ctx.session
    settings = get_settings()
    profile = resolve_embedding_profile(settings)  # registry lookup only, no model load
    repo = build_qdrant_repository(settings, profile)

    dropped = await drop_stale_collections(
        repo, active=repo.collection, base_prefix=f"{settings.qdrant_collection}__"
    )
    orphans = await remove_orphan_content(ctx, repo)

    log.info(
        "remove_stale_vectors",
        job_id=str(ctx.job.id),
        active_collection=repo.collection,
        dropped_collections=len(dropped),
        orphan_content_removed=orphans,
    )
    await ctx.engine.complete(
        session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
    )
    await session.commit()
