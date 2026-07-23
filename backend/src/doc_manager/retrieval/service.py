"""Vector retrieval for ``/search`` (TECHSTACK sections 5.9, 8; Phase 4.d).

Embeds the query, restricts candidates to the content objects that satisfy the
SQL filters (source/extension/document), runs the Qdrant search, then resolves
each hit's current display paths and availability from PostgreSQL — never from the
vector payload — so a moved or renamed file yields a fresh, correct citation.

No generation provider is touched here; search stays functional with generation
disabled (Phase 4 exit criterion 3).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.core.display import display_path
from doc_manager.db.models import CatalogEntry, FileVersion, SourceLocation
from doc_manager.domain.enums import CatalogEntryState
from doc_manager.vectors import QdrantRepository

#: How the entry's catalog state maps to a citation's availability.
_AVAILABILITY = {
    CatalogEntryState.indexed.value: "current",
    CatalogEntryState.missing.value: "missing",
}
_SNIPPET_CHARS = 320


class QueryEmbedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    catalog_entry_id: str
    source_location_id: str
    display_path: str
    state: str
    is_primary: bool


@dataclass(frozen=True, slots=True)
class SearchResult:
    chunk_id: str
    content_object_id: str
    score: float
    page_start: int | None
    page_end: int | None
    snippet: str
    availability: str
    paths: list[ResolvedPath] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchFilters:
    source_location_ids: Sequence[uuid.UUID] | None = None
    extensions: Sequence[str] | None = None
    document_ids: Sequence[uuid.UUID] | None = None

    def is_empty(self) -> bool:
        return not (self.source_location_ids or self.extensions or self.document_ids)


class RetrievalService:
    def __init__(self, embedding: QueryEmbedder, repo: QdrantRepository) -> None:
        self._embedding = embedding
        self._repo = repo

    async def search(
        self,
        session: AsyncSession,
        *,
        query: str,
        filters: SearchFilters | None = None,
        top_k: int,
        score_threshold: float | None = None,
    ) -> list[SearchResult]:
        filters = filters or SearchFilters()
        allowed: list[uuid.UUID] | None = None
        if not filters.is_empty():
            allowed = await self._allowed_content_ids(session, filters)
            if not allowed:
                return []  # filters exclude everything; no vector query needed.

        query_vector = self._embedding.embed_query(query)
        hits = await self._repo.search(
            query_vector,
            top_k=top_k,
            score_threshold=score_threshold,
            content_object_ids=allowed,
        )
        if not hits:
            return []

        paths_by_content = await self._resolve_paths(
            session, {uuid.UUID(h.content_object_id) for h in hits}
        )
        results = []
        for hit in hits:
            paths = paths_by_content.get(hit.content_object_id, [])
            results.append(
                SearchResult(
                    chunk_id=hit.chunk_id,
                    content_object_id=hit.content_object_id,
                    score=hit.score,
                    page_start=hit.page_start,
                    page_end=hit.page_end,
                    snippet=_snippet(hit.text),
                    availability=_availability(paths),
                    paths=paths,
                )
            )
        return results

    async def _allowed_content_ids(
        self, session: AsyncSession, filters: SearchFilters
    ) -> list[uuid.UUID]:
        """Content objects whose current catalog entry satisfies every filter."""
        stmt = (
            select(FileVersion.content_object_id)
            .join(CatalogEntry, CatalogEntry.current_file_version_id == FileVersion.id)
            .where(FileVersion.content_object_id.is_not(None))
        )
        if filters.source_location_ids:
            stmt = stmt.where(CatalogEntry.source_location_id.in_(filters.source_location_ids))
        if filters.extensions:
            stmt = stmt.where(CatalogEntry.extension.in_([e.lower() for e in filters.extensions]))
        if filters.document_ids:
            stmt = stmt.where(CatalogEntry.id.in_(filters.document_ids))
        rows = (await session.scalars(stmt.distinct())).all()
        return [r for r in rows if r is not None]

    async def _resolve_paths(
        self, session: AsyncSession, content_ids: set[uuid.UUID]
    ) -> dict[str, list[ResolvedPath]]:
        """Current display paths per content object, primary first."""
        if not content_ids:
            return {}
        stmt = (
            select(FileVersion.content_object_id, CatalogEntry, SourceLocation)
            .join(CatalogEntry, CatalogEntry.current_file_version_id == FileVersion.id)
            .join(SourceLocation, SourceLocation.id == CatalogEntry.source_location_id)
            .where(FileVersion.content_object_id.in_(content_ids))
            .order_by(CatalogEntry.created_at.asc(), CatalogEntry.id.asc())
        )
        by_content: dict[str, list[ResolvedPath]] = {}
        for content_id, entry, location in (await session.execute(stmt)).all():
            bucket = by_content.setdefault(str(content_id), [])
            bucket.append(
                ResolvedPath(
                    catalog_entry_id=str(entry.id),
                    source_location_id=str(entry.source_location_id),
                    display_path=display_path(
                        location.path_style, location.display_root, entry.relative_path
                    ),
                    state=entry.state,
                    is_primary=not bucket,  # first row for this content is primary.
                )
            )
        return by_content


def _snippet(text: str) -> str:
    text = text.strip()
    if len(text) <= _SNIPPET_CHARS:
        return text
    return text[:_SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"


def _availability(paths: list[ResolvedPath]) -> str:
    """A hit is current/missing per its primary path; historical if none remain."""
    if not paths:
        return "historical"
    primary = paths[0]
    return _AVAILABILITY.get(primary.state, "historical")
