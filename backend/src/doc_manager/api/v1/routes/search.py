"""Vector search route (TECHSTACK sections 5.9, 8; Phase 4.d).

``POST /api/v1/search`` embeds the query, filters candidates via SQL, searches
Qdrant, and resolves current display paths from PostgreSQL. It invokes no
generation provider — search stays available with generation disabled (Phase 4
exit criterion 3).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.api.dependencies import get_retrieval_service, get_session, get_settings_dep
from doc_manager.api.envelope import envelope
from doc_manager.api.errors import Problem
from doc_manager.api.serializers import serialize_search_result
from doc_manager.core.config import Settings
from doc_manager.retrieval import RetrievalService, SearchFilters

router = APIRouter(prefix="/search", tags=["search"])

_MAX_TOP_K = 100
_MAX_QUERY_CHARS = 2000


class SearchFiltersBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Arrays must be omitted rather than empty (contract sec. 5.3); min_length=1
    # rejects an explicit empty array.
    source_location_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)
    extensions: list[str] | None = Field(default=None, min_length=1)
    document_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)


class SearchRetrievalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: int | None = Field(default=None, ge=1, le=_MAX_TOP_K)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=_MAX_QUERY_CHARS)
    filters: SearchFiltersBody | None = None
    retrieval: SearchRetrievalBody | None = None


@router.post("")
async def search(
    request: Request,
    body: SearchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: Annotated[RetrievalService, Depends(get_retrieval_service)],
) -> dict[str, Any]:
    if not body.query.strip():
        raise Problem(422, "validation_failed", "query must not be blank.")

    retrieval = body.retrieval or SearchRetrievalBody()
    top_k = retrieval.top_k or settings.search_top_k
    threshold = (
        retrieval.score_threshold
        if retrieval.score_threshold is not None
        else settings.search_score_threshold
    )
    filters = SearchFilters(
        source_location_ids=body.filters.source_location_ids if body.filters else None,
        extensions=body.filters.extensions if body.filters else None,
        document_ids=body.filters.document_ids if body.filters else None,
    )

    results = await service.search(
        session,
        query=body.query,
        filters=filters,
        top_k=top_k,
        score_threshold=threshold,
    )
    return envelope(
        request,
        {
            "results": [serialize_search_result(r) for r in results],
            "result_count": len(results),
            "top_k": top_k,
        },
    )
