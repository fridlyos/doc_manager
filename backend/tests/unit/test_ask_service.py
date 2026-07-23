"""AskService orchestration (Phase 5.f): states, citations, external policy.

Offline — fake retrieval + fake providers; the external source-policy DB lookup is
stubbed. Verifies completed/insufficient/refused, server-owned citations, and the
external denied/confirmation/allowed outcomes with correct data-boundary counters.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from doc_manager.core.config import Settings
from doc_manager.generation.ask import AskRequest, AskResult, AskService
from doc_manager.generation.base import DataBoundary, ProviderCapabilities, ProviderReadiness
from doc_manager.generation.errors import GenerationError, GenerationErrorCode
from doc_manager.generation.events import (
    FinishReason,
    GenDelta,
    GenerationEvent,
    GenFinished,
    GenRefusal,
    GenStarted,
    GenUsage,
    Usage,
)
from doc_manager.retrieval.service import ResolvedPath, SearchResult


def _result(text: str, *, source: str = "loc-1", chunk: str = "c1") -> SearchResult:
    return SearchResult(
        chunk_id=chunk,
        content_object_id="obj-1",
        score=0.8,
        page_start=4,
        page_end=4,
        text=text,
        snippet=text[:40],
        availability="current",
        paths=[
            ResolvedPath(
                catalog_entry_id="e1",
                source_location_id=source,
                display_path="/docs/a.txt",
                state="indexed",
                is_primary=True,
            )
        ],
    )


class FakeRetrieval:
    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results

    async def search(self, session: Any, **kwargs: Any) -> list[SearchResult]:
        return self._results


class FakeProvider:
    def __init__(
        self,
        boundary: DataBoundary,
        events: list[GenerationEvent] | None = None,
        *,
        provider_id: str = "p",
    ) -> None:
        self.provider_id = provider_id
        self.data_boundary = boundary
        self.capabilities = ProviderCapabilities(context_tokens=8192, max_output_tokens=512)
        self._events = events or []
        self.called = False

    async def readiness(self) -> ProviderReadiness:  # pragma: no cover
        return ProviderReadiness(ready=True)

    def secret_available(self, settings: Settings) -> bool:
        return True

    async def generate(self, request: Any) -> AsyncIterator[GenerationEvent]:
        self.called = True
        for event in self._events:
            yield event


def _service(results: list[SearchResult], **settings_kw: Any) -> AskService:
    return AskService(FakeRetrieval(results), Settings(**settings_kw))  # type: ignore[arg-type]


def _answer_events(text: str) -> list[GenerationEvent]:
    return [
        GenStarted("p", "m", "local"),
        GenDelta(text),
        GenUsage(Usage(10, 5, 15)),
        GenFinished(FinishReason.stop, Usage(10, 5, 15)),
    ]


async def _ask(service: AskService, provider: FakeProvider, **req_kw: Any) -> AskResult:
    request = AskRequest(question="when does it renew?", provider_id="p", **req_kw)
    return await service.ask(object(), request, provider)  # type: ignore[arg-type]


async def test_completed_local_with_citation() -> None:
    service = _service([_result("The office opens at 9am.")])
    provider = FakeProvider(DataBoundary.local, _answer_events("It opens at 9am [E1]."))
    result = await _ask(service, provider)
    assert result.status == "completed"
    assert result.answer == "It opens at 9am [1]."
    assert [c.ordinal for c in result.citations] == [1]
    assert result.citations[0].chunk_id == "c1"
    assert result.invoked is True
    assert result.data_boundary.classification == "local"
    assert result.usage is not None and result.usage.total_tokens == 15


async def test_insufficient_when_no_evidence_skips_provider() -> None:
    service = _service([])
    provider = FakeProvider(DataBoundary.local, _answer_events("should not run"))
    result = await _ask(service, provider)
    assert result.status == "insufficient_evidence"
    assert result.invoked is False
    assert result.answer is None
    assert provider.called is False


async def test_model_declared_insufficient() -> None:
    service = _service([_result("unrelated text")])
    provider = FakeProvider(
        DataBoundary.local,
        [
            GenStarted("p", "m", "local"),
            GenDelta("INSUFFICIENT_EVIDENCE"),
            GenFinished(FinishReason.stop),
        ],
    )
    result = await _ask(service, provider)
    assert result.status == "insufficient_evidence"
    assert result.invoked is True


async def test_refusal() -> None:
    service = _service([_result("evidence")])
    provider = FakeProvider(
        DataBoundary.local, [GenStarted("p", "m", "local"), GenRefusal("I won't answer that.")]
    )
    result = await _ask(service, provider)
    assert result.status == "refused"
    assert result.answer is None
    assert result.finish_reason == "refusal"


async def test_unknown_alias_warning() -> None:
    service = _service([_result("evidence one")])
    provider = FakeProvider(DataBoundary.local, _answer_events("cite [E1] and [E9]"))
    result = await _ask(service, provider)
    assert "unknown_provider_citation_removed" in result.warnings
    assert [c.citation_id for c in result.citations] == ["E1"]


async def _stub_policies(service: AskService, policies: Sequence[str]) -> None:
    async def fake(session: Any, evidence: Any) -> list[str]:
        return list(policies)

    setattr(service, "_source_policies", fake)  # noqa: B010 - stub a private method


async def test_external_denied_raises() -> None:
    service = _service([_result("evidence")], external_llm_enabled=True)
    await _stub_policies(service, ["deny"])
    provider = FakeProvider(DataBoundary.external, _answer_events("x [E1]"))
    with pytest.raises(GenerationError) as exc:
        await _ask(service, provider, external_processing_acknowledged=True)
    assert exc.value.code is GenerationErrorCode.external_policy_denied
    assert provider.called is False


async def test_external_confirmation_required_skips_provider() -> None:
    service = _service([_result("evidence")], external_llm_enabled=True)
    await _stub_policies(service, ["allow"])
    provider = FakeProvider(DataBoundary.external, _answer_events("x [E1]"))
    result = await _ask(service, provider, external_processing_acknowledged=False)
    assert result.status == "external_confirmation_required"
    assert result.confirmation is not None
    assert result.confirmation["evidence_blocks"] == 1
    assert result.confirmation["paths_sent"] == 0
    assert provider.called is False
    assert result.invoked is False


async def test_external_allowed_reports_transfer_no_metadata() -> None:
    service = _service([_result("evidence")], external_llm_enabled=True)
    await _stub_policies(service, ["allow"])
    provider = FakeProvider(DataBoundary.external, _answer_events("answer [E1]"))
    result = await _ask(service, provider, external_processing_acknowledged=True)
    assert result.status == "completed"
    assert result.data_boundary.classification == "external"
    assert result.data_boundary.external_transfer_occurred is True
    payload = result.data_boundary.external_payload
    assert payload.evidence_blocks_sent == 1
    assert payload.paths_sent == 0 and payload.file_names_sent == 0
