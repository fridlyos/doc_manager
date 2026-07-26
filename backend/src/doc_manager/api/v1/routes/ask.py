"""Ask + provider routes (TECHSTACK 5.13; contract §8). Phase 5.f.

``POST /ask`` returns the normal §8.2 result. ``POST /ask/stream`` streams the
normalized §8.3 SSE events. Provider selection and request validation happen
before any SSE header is committed, so those failures are ordinary Problem
responses; once streaming begins, failures become a terminal ``ask.error`` event.
No generation provider is contacted for ``/search`` — Ask is the only route that
does, and only after policy allows it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.api.dependencies import (
    get_ask_service,
    get_provider_registry,
    get_session,
    get_settings_dep,
)
from doc_manager.api.envelope import envelope
from doc_manager.api.errors import Problem, problem_body
from doc_manager.api.serializers import serialize_ask_result, serialize_citation
from doc_manager.api.sse import SSE_HEADERS, common_fields, sse_event
from doc_manager.core.config import Settings
from doc_manager.generation.ask import (
    AnswerDelta,
    AskRequest,
    AskResultEvent,
    AskService,
    AskStarted,
    AskWarning,
    CitationResolved,
    GenerationStarted,
    RetrievalCompleted,
)
from doc_manager.generation.errors import GenerationError
from doc_manager.generation.registry import ProviderRegistry
from doc_manager.retrieval.service import SearchFilters

router = APIRouter(tags=["ask"])

_MAX_QUESTION_CHARS = 4000
_MAX_TOP_K = 100


class AskFiltersBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_location_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)
    extensions: list[str] | None = Field(default=None, min_length=1)
    document_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)


class AskRetrievalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: int | None = Field(default=None, ge=1, le=_MAX_TOP_K)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=_MAX_QUESTION_CHARS)
    provider_id: str = Field(min_length=1, max_length=64)
    model_id: str | None = Field(default=None, max_length=128)
    external_processing_acknowledged: bool = False
    filters: AskFiltersBody | None = None
    retrieval: AskRetrievalBody | None = None


def _ask_request(body: AskBody) -> AskRequest:
    if not body.question.strip():
        raise Problem(422, "validation_failed", "question must not be blank.")
    filters = None
    if body.filters is not None:
        filters = SearchFilters(
            source_location_ids=body.filters.source_location_ids,
            extensions=body.filters.extensions,
            document_ids=body.filters.document_ids,
        )
    retrieval = body.retrieval or AskRetrievalBody()
    return AskRequest(
        question=body.question,
        provider_id=body.provider_id,
        external_processing_acknowledged=body.external_processing_acknowledged,
        filters=filters,
        top_k=retrieval.top_k,
        score_threshold=retrieval.score_threshold,
        model_id=body.model_id,
    )


def _select_provider(registry: ProviderRegistry, settings: Settings, provider_id: str) -> Any:
    """Resolve the provider before any streaming begins; map to Problem."""
    try:
        return registry.require_eligible(settings, provider_id)
    except GenerationError as exc:
        raise _problem(exc) from exc


def _problem(exc: GenerationError) -> Problem:
    return Problem(exc.http_status, exc.code.value, exc.message, retryable=exc.retryable)


@router.post("/ask")
async def ask(
    request: Request,
    body: AskBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    service: Annotated[AskService, Depends(get_ask_service)],
) -> dict[str, Any]:
    ask_req = _ask_request(body)
    provider = _select_provider(registry, settings, ask_req.provider_id)
    try:
        result = await service.ask(session, ask_req, provider)
    except GenerationError as exc:
        raise _problem(exc) from exc
    return envelope(request, serialize_ask_result(result))


@router.post("/ask/stream")
async def ask_stream(
    request: Request,
    body: AskBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    service: Annotated[AskService, Depends(get_ask_service)],
) -> StreamingResponse:
    # Pre-stream: validation + provider eligibility are ordinary Problems.
    ask_req = _ask_request(body)
    provider = _select_provider(registry, settings, ask_req.provider_id)
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    ask_id = str(uuid.uuid4())

    async def frames() -> AsyncIterator[str]:
        seq = 0

        def frame(event_name: str, payload: dict[str, Any]) -> str:
            nonlocal seq
            seq += 1
            data = {
                **common_fields(
                    sequence=seq,
                    request_id=request_id,
                    ask_id=ask_id,
                    occurred_at=datetime.now(UTC).isoformat(),
                ),
                **payload,
            }
            return sse_event(event_id=seq, event=event_name, data=data)

        try:
            async for event in service.ask_stream(session, ask_req, provider):
                yield _render(event, frame)
        except GenerationError as exc:
            yield frame("ask.error", {"problem": problem_body(request, _problem(exc))})

    headers = {**SSE_HEADERS, "X-Request-ID": request_id, "Docman-Api-Version": "1"}
    return StreamingResponse(frames(), media_type="text/event-stream", headers=headers)


def _render(event: Any, frame: Callable[[str, dict[str, Any]], str]) -> str:
    if isinstance(event, AskStarted):
        return frame(
            "ask.started",
            {
                "provider": {
                    "provider_id": event.provider_id,
                    "model_id": event.model_id,
                    "data_boundary": event.data_boundary,
                }
            },
        )
    if isinstance(event, RetrievalCompleted):
        return frame(
            "retrieval.completed",
            {
                "retrieval": {
                    "candidate_count": event.candidate_count,
                    "selected_evidence_count": event.selected_count,
                    "sufficient": event.sufficient,
                }
            },
        )
    if isinstance(event, GenerationStarted):
        return frame(
            "generation.started",
            {
                "provider": {
                    "provider_id": event.provider_id,
                    "model_id": event.model_id,
                    "data_boundary": event.data_boundary,
                }
            },
        )
    if isinstance(event, AnswerDelta):
        return frame("answer.delta", {"delta": event.text})
    if isinstance(event, CitationResolved):
        return frame("citation.resolved", {"citation": serialize_citation(event.citation)})
    if isinstance(event, AskWarning):
        return frame("ask.warning", {"code": event.code, "message": event.code})
    if isinstance(event, AskResultEvent):
        return frame("ask.result", {"data": serialize_ask_result(event.result)})
    raise AssertionError(f"unhandled ask event: {type(event).__name__}")


# --- provider discovery -----------------------------------------------------

providers_router = APIRouter(prefix="/system", tags=["system"])


@providers_router.get("/providers")
async def list_providers(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
) -> dict[str, Any]:
    eligible = set(registry.eligible_ids(settings))
    data = [
        {
            "provider_id": p.provider_id,
            "data_boundary": p.data_boundary.value,
            "eligible": p.provider_id in eligible,
        }
        for p in registry.all()
    ]
    return envelope(request, data)


@providers_router.post("/providers/{provider_id}/test")
async def test_provider(
    request: Request,
    provider_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
) -> dict[str, Any]:
    """Live readiness probe. Uses no document content (contract §8)."""
    try:
        provider = registry.get(provider_id)
    except GenerationError as exc:
        raise _problem(exc) from exc
    readiness = await provider.readiness()
    return envelope(
        request,
        {
            "provider_id": provider_id,
            "data_boundary": provider.data_boundary.value,
            "eligible": registry.is_eligible(settings, provider),
            "ready": readiness.ready,
            "detail": readiness.detail,
            "model_id": readiness.model_id,
        },
    )
