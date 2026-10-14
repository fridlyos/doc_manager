"""Vector retrieval for ``/search`` (TECHSTACK sections 5.9, 8; Phase 4.d).

Embeds the query, restricts candidates to the content objects that satisfy the
SQL filters (source/extension/document), runs the Qdrant search, then resolves
each hit's current display paths and availability from PostgreSQL — never from the
vector payload — so a moved or renamed file yields a fresh, correct citation.

No generation provider is touched here; search stays functional with generation
disabled (Phase 4 exit criterion 3).

File names are searched first: when a file's *name* clearly matches the query
(its own vector in the name index), that file's best passages lead the results,
ahead of the regular hybrid ranking. Only entries that are currently ``indexed``
qualify — a failed original never shadows a copy that indexed fine.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.core.display import display_path
from doc_manager.db.models import CatalogEntry, Chunk, FileVersion, SourceLocation
from doc_manager.domain.enums import CatalogEntryState
from doc_manager.vectors import NameIndex, QdrantRepository, SearchHit

#: How the entry's catalog state maps to a citation's availability.
_AVAILABILITY = {
    CatalogEntryState.indexed.value: "current",
    CatalogEntryState.missing.value: "missing",
}
_SNIPPET_CHARS = 320

#: Keyword-leg synonyms. A short, user-phrasing → document-phrasing map so a casual
#: question surfaces the chunk even when the document uses a different word (e.g.
#: "job" → "role"/"position"). Only the keyword (OR) leg sees these; the vector is
#: still embedded from the raw question. Extend conservatively — each entry widens
#: the keyword match, so keep it to terms whose synonyms are genuinely equivalent.
_KEYWORD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "job": ("role", "position", "employment", "experience"),
    "jobs": ("roles", "positions", "employment", "experience"),
    "work": ("employment", "experience", "role"),
    "employer": ("company", "organization"),
    "salary": ("compensation", "pay", "wage"),
    "resume": ("cv", "curriculum"),
    "phone": ("mobile", "cell", "telephone"),
    "address": ("street", "city"),
}
#: Filler words dropped from the keyword leg. That leg matches chunks containing
#: *any* term, so words like "there"/"about" would match nearly every chunk and
#: wash out the boost for the words that carry the question. Question words and
#: pronouns only; nothing that could name a document's subject.
_KEYWORD_STOPWORDS = frozenset(
    [
        "a",
        "about",
        "all",
        "am",
        "an",
        "and",
        "any",
        "anything",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "here",
        "him",
        "his",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "she",
        "so",
        "some",
        "something",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "too",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)
_POSSESSIVE_RE = re.compile(r"['’]s\b", re.UNICODE)
_WORD_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)


def content_terms(text: str) -> list[str]:
    """Lowercased words of ``text`` with possessives and stopwords removed."""
    base = _POSSESSIVE_RE.sub("", text.lower())
    return [t for t in _WORD_RE.findall(base) if t not in _KEYWORD_STOPWORDS]


def expand_keyword_query(query: str) -> str:
    """Query string for the keyword (hybrid) leg: possessives and stopwords stripped,
    synonyms added.

    Returns a space-joined term list; the repository re-tokenises it. The vector
    leg keeps using the untouched question, so synonyms only ever *widen* which
    chunks the keyword leg can match — they never move the semantic anchor.
    """
    terms = content_terms(query)
    expanded = list(terms)
    for term in terms:
        for synonym in _KEYWORD_SYNONYMS.get(term, ()):
            if synonym not in expanded:
                expanded.append(synonym)
    return " ".join(expanded)


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
    #: The full chunk text (from the vector payload). Used as grounding evidence by
    #: the Ask/RAG layer; the ``/search`` serializer exposes only ``snippet``.
    text: str
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


@dataclass(frozen=True, slots=True)
class NameMatchConfig:
    """When a file-name match is strong enough to lead the results (see Settings)."""

    min_score: float = 0.75
    margin: float = 0.05
    max_files: int = 3
    chunks_per_file: int = 2
    #: Name candidates fetched before the score/margin cut.
    candidates: int = 10


class RetrievalService:
    def __init__(
        self,
        embedding: QueryEmbedder,
        repo: QdrantRepository,
        names: NameIndex | None = None,
        name_match: NameMatchConfig | None = None,
    ) -> None:
        self._embedding = embedding
        self._repo = repo
        self._names = names
        self._name_match = name_match or NameMatchConfig()

    async def search(
        self,
        session: AsyncSession,
        *,
        query: str,
        filters: SearchFilters | None = None,
        top_k: int,
        score_threshold: float | None = None,
        content_object_ids: Sequence[str] | None = None,
    ) -> list[SearchResult]:
        """Ranked chunks for ``query``.

        ``content_object_ids`` narrows the search to those documents (on top of
        ``filters``); chat uses it to keep a follow-up within the documents the
        conversation was already about.
        """
        filters = filters or SearchFilters()
        allowed: list[uuid.UUID | str] | None = None
        if not filters.is_empty():
            allowed = list(await self._allowed_content_ids(session, filters))
        if content_object_ids is not None:
            scope = {str(c) for c in content_object_ids}
            allowed = (
                [c for c in allowed if str(c) in scope] if allowed is not None else list(scope)
            )
        if allowed is not None and not allowed:
            return []  # filters exclude everything; no vector query needed.

        query_vector = self._embedding.embed_query(query)
        keyword_query = expand_keyword_query(query)
        hits = await self._repo.search(
            query_vector,
            top_k=top_k,
            score_threshold=score_threshold,
            content_object_ids=allowed,
            # Hybrid: fuse keyword matches (names, IDs, synonyms) with the vector
            # results so an exact/related term surfaces even on a weak vector score.
            keyword_query=keyword_query,
        )
        name_docs = await self._name_matches(session, query_vector, allowed)
        if name_docs:
            led = await self._repo.search(
                query_vector,
                top_k=top_k,
                score_threshold=score_threshold,
                content_object_ids=name_docs,
                keyword_query=keyword_query,
            )
            hits = _name_first(led, hits, name_docs, self._name_match.chunks_per_file, top_k)
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
                    text=hit.text,
                    snippet=_snippet(hit.text),
                    availability=_availability(paths),
                    paths=paths,
                )
            )
        return results

    async def _name_matches(
        self,
        session: AsyncSession,
        query_vector: list[float],
        allowed: Sequence[uuid.UUID | str] | None,
    ) -> list[str]:
        """Content objects of files whose name clearly matches, best first.

        A name counts when it scores at least ``min_score`` and is within
        ``margin`` of the best name score. Hits are re-checked against PostgreSQL:
        the entry must be ``indexed`` *now* and its current content is used, so a
        stale name point (renamed, failed, deleted) can never lead the results.
        """
        if self._names is None:
            return []
        cfg = self._name_match
        hits = await self._names.search(query_vector, top_k=cfg.candidates)
        if not hits:
            return []
        floor = max(cfg.min_score, hits[0].score - cfg.margin)
        strong = [h for h in hits if h.score >= floor]
        if not strong:
            return []
        entry_ids = []
        for h in strong:
            try:
                entry_ids.append(uuid.UUID(h.catalog_entry_id))
            except ValueError:
                continue
        rows = await session.execute(
            select(CatalogEntry.id, FileVersion.content_object_id)
            .join(FileVersion, FileVersion.id == CatalogEntry.current_file_version_id)
            .where(
                CatalogEntry.id.in_(entry_ids),
                CatalogEntry.state == CatalogEntryState.indexed.value,
                FileVersion.content_object_id.is_not(None),
            )
        )
        content_of = {str(eid): str(cid) for eid, cid in rows.all()}
        scope = {str(c) for c in allowed} if allowed is not None else None
        ordered: list[str] = []
        for h in strong:
            cid = content_of.get(h.catalog_entry_id)
            if cid is None or cid in ordered or (scope is not None and cid not in scope):
                continue
            ordered.append(cid)
            if len(ordered) >= cfg.max_files:
                break
        return ordered

    async def content_ids_for_chunks(
        self, session: AsyncSession, chunk_ids: Sequence[str]
    ) -> list[str]:
        """Documents (content objects) the given chunks belong to, deduplicated."""
        ids: list[uuid.UUID] = []
        for raw in chunk_ids:
            try:
                ids.append(uuid.UUID(str(raw)))
            except ValueError:
                continue
        if not ids:
            return []
        rows = await session.scalars(
            select(Chunk.content_object_id).where(Chunk.id.in_(ids)).distinct()
        )
        return [str(r) for r in rows.all()]

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


def _name_first(
    led: list[SearchHit],
    hits: list[SearchHit],
    name_docs: list[str],
    per_file: int,
    top_k: int,
) -> list[SearchHit]:
    """Name-matched files' best passages first (in name-match order), then the rest."""
    rank = {cid: i for i, cid in enumerate(name_docs)}
    taken: dict[str, int] = {}
    first: list[SearchHit] = []
    for hit in led:  # already ranked by relevance to the query
        if taken.get(hit.content_object_id, 0) >= per_file:
            continue
        taken[hit.content_object_id] = taken.get(hit.content_object_id, 0) + 1
        first.append(hit)
    first.sort(key=lambda h: rank.get(h.content_object_id, len(rank)))  # stable
    seen = {h.chunk_id for h in first}
    rest = [h for h in hits if h.chunk_id not in seen]
    return (first + rest)[:top_k]


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
