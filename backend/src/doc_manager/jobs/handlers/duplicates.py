"""`build_duplicate_report` handler (TECHSTACK 5.4, §14 Phase 6.c).

Rebuilds the materialized duplicate report from authoritative hashes over the
currently-indexed catalog:

* **exact** groups — active entries whose current file version shares a ``sha256``
  (byte-identical files), ≥2 members;
* **text** groups — active entries whose current content object shares a
  ``text_hash`` across **≥2 distinct file hashes** (text-equivalent, including
  different pagination), ≥2 members.

A full truncate-and-rebuild, so the report is always safely reconstructable and
never drifts from the catalog. Each member carries its server-resolved display
path, location, and state, so a group lists every active location/path.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import delete, select

from doc_manager.core.display import display_path
from doc_manager.core.logging import get_logger
from doc_manager.db.models import (
    CatalogEntry,
    ContentObject,
    DuplicateGroup,
    DuplicateMember,
    FileVersion,
    SourceLocation,
)
from doc_manager.domain.enums import CatalogEntryState
from doc_manager.jobs.context import JobContext

log = get_logger("doc_manager.jobs.duplicates")


@dataclass(frozen=True, slots=True)
class _Member:
    catalog_entry_id: uuid.UUID
    source_location_id: uuid.UUID
    display_path: str
    state: str
    sha256: str
    text_hash: str


async def handle_build_duplicate_report(ctx: JobContext) -> None:
    session = ctx.session
    members = await _load_members(ctx)

    # Full rebuild: cascade drops members with their groups.
    await session.execute(delete(DuplicateGroup))

    exact = _group_exact(members)
    text = _group_text(members)

    written = 0
    for kind, groups in (("exact", exact), ("text", text)):
        for group_hash, group_members in groups:
            group = DuplicateGroup(
                kind=kind, group_hash=group_hash, member_count=len(group_members)
            )
            session.add(group)
            await session.flush()
            for m in group_members:
                session.add(
                    DuplicateMember(
                        group_id=group.id,
                        catalog_entry_id=m.catalog_entry_id,
                        source_location_id=m.source_location_id,
                        display_path=m.display_path,
                        state=m.state,
                        sha256=m.sha256,
                    )
                )
            written += 1

    log.info(
        "build_duplicate_report",
        job_id=str(ctx.job.id),
        exact_groups=len(exact),
        text_groups=len(text),
        groups=written,
    )
    await ctx.engine.complete(
        session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
    )
    await session.commit()


async def _load_members(ctx: JobContext) -> list[_Member]:
    rows = (
        await ctx.session.execute(
            select(
                CatalogEntry.id,
                CatalogEntry.source_location_id,
                CatalogEntry.relative_path,
                CatalogEntry.state,
                SourceLocation.path_style,
                SourceLocation.display_root,
                FileVersion.sha256,
                ContentObject.text_hash,
            )
            .join(FileVersion, CatalogEntry.current_file_version_id == FileVersion.id)
            .join(SourceLocation, SourceLocation.id == CatalogEntry.source_location_id)
            .join(ContentObject, FileVersion.content_object_id == ContentObject.id)
            .where(CatalogEntry.state == CatalogEntryState.indexed.value)
        )
    ).all()
    return [
        _Member(
            catalog_entry_id=r[0],
            source_location_id=r[1],
            display_path=display_path(r[4], r[5], r[2]),
            state=r[3],
            sha256=r[6],
            text_hash=r[7],
        )
        for r in rows
    ]


def _group_exact(members: list[_Member]) -> list[tuple[str, list[_Member]]]:
    by_hash: dict[str, list[_Member]] = defaultdict(list)
    for m in members:
        by_hash[m.sha256].append(m)
    return [(h, ms) for h, ms in by_hash.items() if len(ms) >= 2]


def _group_text(members: list[_Member]) -> list[tuple[str, list[_Member]]]:
    by_text: dict[str, list[_Member]] = defaultdict(list)
    for m in members:
        by_text[m.text_hash].append(m)
    groups = []
    for text_hash, ms in by_text.items():
        # A text duplicate requires distinct file hashes (byte-different but
        # text-equivalent, incl. different pagination); pure byte-identical sets
        # are already reported as exact.
        if len(ms) >= 2 and len({m.sha256 for m in ms}) >= 2:
            groups.append((text_hash, ms))
    return groups
