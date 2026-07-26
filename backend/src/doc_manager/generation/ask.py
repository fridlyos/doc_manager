"""Ask orchestration (TECHSTACK 5.13; contract §8). Phase 5.f.

Ties Phase 4 retrieval and the Phase 5 provider/policy/RAG pieces into one grounded,
cited answer — as a normal result (`ask`) or a normalized event stream
(`ask_stream`). No provider SDK types cross this boundary; citations are resolved
server-side; external transfer is gated by the policy and accounted in the data
boundary.

Ordering mirrors contract §8.3: provider selection is done by the caller *before*
committing SSE headers (so `provider_unavailable` is an ordinary Problem); this
service then does retrieval, evidence selection, the external-policy check, and
generation, emitting exactly one terminal outcome.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.core.config import Settings
from doc_manager.db.models import SourceLocation
from doc_manager.generation.base import DataBoundary, GenerationProvider
from doc_manager.generation.boundary import (
    DataBoundaryReport,
    confirmation_summary,
    external_boundary,
    local_boundary,
)
from doc_manager.generation.events import (
    GenDelta,
    GenFinished,
    GenRefusal,
    GenStarted,
    GenUsage,
    Usage,
)
from doc_manager.generation.policy import ExternalDecision, evaluate_external_policy
from doc_manager.generation.rag import (
    Citation,
    EvidenceSet,
    build_grounded_prompt,
    is_insufficient,
    map_citations,
    select_evidence,
)
from doc_manager.generation.timeout import stream_with_timeout
from doc_manager.retrieval.service import RetrievalService, SearchFilters

_CONTEXT_BUFFER_TOKENS = 512
_MIN_EVIDENCE_BUDGET = 256


@dataclass(frozen=True, slots=True)
class AskRequest:
    question: str
    provider_id: str
    external_processing_acknowledged: bool = False
    filters: SearchFilters | None = None
    top_k: int | None = None
    score_threshold: float | None = None
    model_id: str | None = None


@dataclass(slots=True)
class AskResult:
    id: str
    status: str  # completed | insufficient_evidence | refused | external_confirmation_required
    provider_id: str
    model_id: str | None
    data_boundary: DataBoundaryReport
    invoked: bool
    candidate_count: int
    selected_count: int
    sufficient: bool
    answer: str | None = None
    citations: list[Citation] = field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage | None = None
    retrieval_ms: int = 0
    generation_ms: int | None = None
    total_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    confirmation: dict[str, object] | None = None


# --- streaming events (mapped to SSE ask.* by the route) --------------------


@dataclass(frozen=True, slots=True)
class AskStarted:
    provider_id: str
    model_id: str | None
    data_boundary: str


@dataclass(frozen=True, slots=True)
class RetrievalCompleted:
    candidate_count: int
    selected_count: int
    sufficient: bool


@dataclass(frozen=True, slots=True)
class GenerationStarted:
    provider_id: str
    model_id: str | None
    data_boundary: str


@dataclass(frozen=True, slots=True)
class AnswerDelta:
    text: str


@dataclass(frozen=True, slots=True)
class CitationResolved:
    citation: Citation


@dataclass(frozen=True, slots=True)
class AskWarning:
    code: str


@dataclass(frozen=True, slots=True)
class AskResultEvent:
    result: AskResult


AskEvent = (
    AskStarted
    | RetrievalCompleted
    | GenerationStarted
    | AnswerDelta
    | CitationResolved
    | AskWarning
    | AskResultEvent
)


class AskService:
    def __init__(self, retrieval: RetrievalService, settings: Settings) -> None:
        self._retrieval = retrieval
        self._settings = settings

    # ------------------------------------------------------------------ ask
    async def ask(
        self, session: AsyncSession, request: AskRequest, provider: GenerationProvider
    ) -> AskResult:
        started = perf_counter()
        prep = await self._prepare(session, request, provider, started)
        if prep.early is not None:
            prep.early.total_ms = _ms(started)
            return prep.early

        answer, usage, finish, refusal, gen_ms = await self._generate(request, provider, prep)
        result = self._finalize(request, provider, prep, answer, usage, finish, refusal, gen_ms)
        result.total_ms = _ms(started)
        return result

    # ----------------------------------------------------------- ask_stream
    async def ask_stream(
        self, session: AsyncSession, request: AskRequest, provider: GenerationProvider
    ) -> AsyncIterator[AskEvent]:
        started = perf_counter()
        model_id = request.model_id or _configured_model(provider, self._settings)
        yield AskStarted(provider.provider_id, model_id, provider.data_boundary.value)

        prep = await self._prepare(session, request, provider, started)
        yield RetrievalCompleted(
            prep.candidate_count, prep.selected_count, not prep.evidence.is_empty
        )
        if prep.early is not None:
            prep.early.total_ms = _ms(started)
            yield AskResultEvent(prep.early)
            return

        request_obj = build_grounded_prompt(
            question=request.question,
            evidence=prep.evidence,
            max_output_tokens=prep.max_output_tokens,
        )
        parts: list[str] = []
        usage: Usage | None = None
        finish: str | None = None
        refusal: str | None = None
        model_seen = model_id
        gen_started = perf_counter()
        gen_events = stream_with_timeout(
            provider.generate(request_obj), timeout_seconds=prep.timeout_seconds
        )
        emitted_generation_started = False
        async for event in gen_events:
            if isinstance(event, GenStarted):
                model_seen = event.model_id
                yield GenerationStarted(event.provider_id, event.model_id, event.data_boundary)
                emitted_generation_started = True
            elif isinstance(event, GenDelta):
                parts.append(event.text)
                yield AnswerDelta(event.text)
            elif isinstance(event, GenUsage):
                usage = event.usage
            elif isinstance(event, GenFinished):
                finish = event.reason.value
                usage = event.usage or usage
            elif isinstance(event, GenRefusal):
                refusal = event.message
        if not emitted_generation_started:
            # Defensive: a provider that streamed no GenStarted still needs the event.
            yield GenerationStarted(provider.provider_id, model_seen, provider.data_boundary.value)

        gen_ms = _ms(gen_started)
        result = self._finalize(
            request, provider, prep, "".join(parts), usage, finish, refusal, gen_ms
        )
        result.model_id = model_seen
        result.total_ms = _ms(started)
        for citation in result.citations:
            yield CitationResolved(citation)
        for code in result.warnings:
            yield AskWarning(code)
        yield AskResultEvent(result)

    # --------------------------------------------------------------- helpers
    async def _prepare(
        self,
        session: AsyncSession,
        request: AskRequest,
        provider: GenerationProvider,
        started: float,
    ) -> _Prepared:
        settings = self._settings
        is_external = provider.data_boundary is DataBoundary.external
        max_output = (
            settings.external_max_output_tokens
            if is_external
            else settings.generation_max_output_tokens
        )
        timeout = float(
            settings.external_request_timeout_seconds
            if is_external
            else settings.generation_request_timeout_seconds
        )
        budget = _evidence_budget(provider, settings, max_output)

        retrieval_start = perf_counter()
        results = await self._retrieval.search(
            session,
            query=request.question,
            filters=request.filters,
            top_k=request.top_k or settings.search_top_k,
            score_threshold=request.score_threshold
            if request.score_threshold is not None
            else settings.search_score_threshold,
        )
        evidence = select_evidence(
            results,
            token_budget=budget,
            max_per_content=settings.ask_max_chunks_per_content,
            max_blocks=settings.ask_max_evidence_blocks,
        )
        retrieval_ms = _ms(retrieval_start)
        model_id = request.model_id or _configured_model(provider, settings)

        prep = _Prepared(
            evidence=evidence,
            candidate_count=len(results),
            selected_count=len(evidence.blocks),
            retrieval_ms=retrieval_ms,
            max_output_tokens=max_output,
            timeout_seconds=timeout,
        )

        if evidence.is_empty:
            prep.early = self._insufficient(request, provider, model_id, prep)
            return prep

        if is_external:
            policies = await self._source_policies(session, evidence)
            outcome = evaluate_external_policy(
                settings=settings,
                provider=provider,
                evidence_source_policies=policies,
                acknowledged=request.external_processing_acknowledged,
            )
            if outcome.decision is ExternalDecision.denied:
                from doc_manager.generation.errors import (
                    GenerationError,
                    GenerationErrorCode,
                )

                raise GenerationError(
                    GenerationErrorCode.external_policy_denied, outcome.reason, retryable=False
                )
            if outcome.decision is ExternalDecision.confirmation_required:
                prep.early = self._confirmation(request, provider, model_id, prep)
        return prep

    async def _generate(
        self, request: AskRequest, provider: GenerationProvider, prep: _Prepared
    ) -> tuple[str, Usage | None, str | None, str | None, int]:
        request_obj = build_grounded_prompt(
            question=request.question,
            evidence=prep.evidence,
            max_output_tokens=prep.max_output_tokens,
        )
        parts: list[str] = []
        usage: Usage | None = None
        finish: str | None = None
        refusal: str | None = None
        gen_started = perf_counter()
        events = stream_with_timeout(
            provider.generate(request_obj), timeout_seconds=prep.timeout_seconds
        )
        async for event in events:
            if isinstance(event, GenStarted):
                prep.model_seen = event.model_id
            elif isinstance(event, GenDelta):
                parts.append(event.text)
            elif isinstance(event, GenUsage):
                usage = event.usage
            elif isinstance(event, GenFinished):
                finish = event.reason.value
                usage = event.usage or usage
            elif isinstance(event, GenRefusal):
                refusal = event.message
        return "".join(parts), usage, finish, refusal, _ms(gen_started)

    def _finalize(
        self,
        request: AskRequest,
        provider: GenerationProvider,
        prep: _Prepared,
        answer: str,
        usage: Usage | None,
        finish: str | None,
        refusal: str | None,
        gen_ms: int,
    ) -> AskResult:
        model_id = prep.model_seen or (
            request.model_id or _configured_model(provider, self._settings)
        )
        is_external = provider.data_boundary is DataBoundary.external

        if refusal is not None:
            return self._base_result(
                provider,
                model_id,
                prep,
                status="refused",
                invoked=True,
                answer=None,
                finish_reason="refusal",
                usage=usage,
                gen_ms=gen_ms,
                attempted_external=is_external,
            )
        if is_insufficient(answer):
            return self._base_result(
                provider,
                model_id,
                prep,
                status="insufficient_evidence",
                invoked=True,
                answer=None,
                finish_reason="insufficient_evidence",
                usage=usage,
                gen_ms=gen_ms,
                attempted_external=is_external,
            )

        mapping = map_citations(answer, prep.evidence)
        result = self._base_result(
            provider,
            model_id,
            prep,
            status="completed",
            invoked=True,
            answer=mapping.answer,
            finish_reason=finish or "stop",
            usage=usage,
            gen_ms=gen_ms,
            attempted_external=is_external,
        )
        result.citations = mapping.citations
        result.warnings = list(mapping.warnings)
        return result

    def _base_result(
        self,
        provider: GenerationProvider,
        model_id: str | None,
        prep: _Prepared,
        *,
        status: str,
        invoked: bool,
        answer: str | None,
        finish_reason: str | None,
        usage: Usage | None,
        gen_ms: int | None,
        attempted_external: bool,
    ) -> AskResult:
        if attempted_external:
            boundary = external_boundary(
                acknowledged=True,
                attempted=invoked,
                occurred=invoked,
                evidence_blocks=prep.selected_count,
                evidence_characters=prep.evidence.character_count,
                citation_ids=prep.selected_count,
            )
        else:
            boundary = local_boundary()
        return AskResult(
            id=str(uuid.uuid4()),
            status=status,
            provider_id=provider.provider_id,
            model_id=model_id,
            data_boundary=boundary,
            invoked=invoked,
            candidate_count=prep.candidate_count,
            selected_count=prep.selected_count,
            sufficient=not prep.evidence.is_empty,
            answer=answer,
            finish_reason=finish_reason,
            usage=usage,
            retrieval_ms=prep.retrieval_ms,
            generation_ms=gen_ms,
        )

    def _insufficient(
        self,
        request: AskRequest,
        provider: GenerationProvider,
        model_id: str | None,
        prep: _Prepared,
    ) -> AskResult:
        is_external = provider.data_boundary is DataBoundary.external
        boundary = (
            external_boundary(
                acknowledged=request.external_processing_acknowledged,
                attempted=False,
                occurred=False,
                evidence_blocks=0,
                evidence_characters=0,
                citation_ids=0,
            )
            if is_external
            else local_boundary()
        )
        return AskResult(
            id=str(uuid.uuid4()),
            status="insufficient_evidence",
            provider_id=provider.provider_id,
            model_id=model_id,
            data_boundary=boundary,
            invoked=False,
            candidate_count=prep.candidate_count,
            selected_count=0,
            sufficient=False,
            finish_reason="insufficient_evidence",
            retrieval_ms=prep.retrieval_ms,
            generation_ms=None,
        )

    def _confirmation(
        self,
        request: AskRequest,
        provider: GenerationProvider,
        model_id: str | None,
        prep: _Prepared,
    ) -> AskResult:
        boundary = external_boundary(
            acknowledged=False,
            attempted=False,
            occurred=False,
            evidence_blocks=0,
            evidence_characters=0,
            citation_ids=0,
        )
        return AskResult(
            id=str(uuid.uuid4()),
            status="external_confirmation_required",
            provider_id=provider.provider_id,
            model_id=model_id,
            data_boundary=boundary,
            invoked=False,
            candidate_count=prep.candidate_count,
            selected_count=prep.selected_count,
            sufficient=True,
            finish_reason=None,
            retrieval_ms=prep.retrieval_ms,
            generation_ms=None,
            confirmation=confirmation_summary(
                provider_id=provider.provider_id,
                evidence_blocks=prep.selected_count,
                evidence_characters=prep.evidence.character_count,
            ),
        )

    async def _source_policies(self, session: AsyncSession, evidence: EvidenceSet) -> list[str]:
        source_ids = {
            uuid.UUID(sid) for block in evidence.blocks for sid in block.source_location_ids
        }
        if not source_ids:
            return []
        rows = (
            await session.execute(
                select(SourceLocation.id, SourceLocation.external_generation_policy).where(
                    SourceLocation.id.in_(source_ids)
                )
            )
        ).all()
        policy_by_source = {str(row[0]): row[1] for row in rows}
        return evidence.evidence_source_policies(policy_by_source)


@dataclass(slots=True)
class _Prepared:
    evidence: EvidenceSet
    candidate_count: int
    selected_count: int
    retrieval_ms: int
    max_output_tokens: int
    timeout_seconds: float
    early: AskResult | None = None
    model_seen: str | None = None


def _evidence_budget(
    provider: GenerationProvider, settings: Settings, max_output_tokens: int
) -> int:
    budget = provider.capabilities.context_tokens - max_output_tokens - _CONTEXT_BUFFER_TOKENS
    if provider.data_boundary is DataBoundary.external:
        budget = min(budget, settings.external_max_evidence_tokens)
    return max(_MIN_EVIDENCE_BUDGET, budget)


def _configured_model(provider: GenerationProvider, settings: Settings) -> str | None:
    if provider.provider_id == "ollama":
        return settings.ollama_chat_model
    if provider.provider_id == "openai":
        return settings.openai_model
    return None


def _ms(since: float) -> int:
    return int((perf_counter() - since) * 1000)
