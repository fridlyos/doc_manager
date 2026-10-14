"""Vector retrieval for search (TECHSTACK sections 5.9, 8; Phase 4.d).

Embeds a query, filters candidates via SQL, searches Qdrant, and resolves current
display paths from PostgreSQL. No generation provider involved.
"""

from doc_manager.retrieval.service import (
    NameMatchConfig,
    QueryEmbedder,
    ResolvedPath,
    RetrievalService,
    SearchFilters,
    SearchResult,
)

__all__ = [
    "NameMatchConfig",
    "QueryEmbedder",
    "ResolvedPath",
    "RetrievalService",
    "SearchFilters",
    "SearchResult",
]
