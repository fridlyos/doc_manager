"""Shared grounding/citation contract across providers (Phase 5.h).

The same scripted model answers are driven through ``AskService`` for **both** the
Ollama adapter (via ``httpx.MockTransport``) and the OpenAI adapter (via a fake
SDK client). Both must yield identical grounding/citation outcomes — same
server-owned citations, ordinal rewriting, unknown-alias handling, and
model-declared insufficiency — proving exit criterion 6. Only the data boundary
differs (local vs external).

Offline: no live Ollama, no OpenAI key. An opt-in live OpenAI smoke is gated by
env at the bottom.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from doc_manager.core.config import Settings
from doc_manager.generation.ask import AskRequest, AskResult, AskService
from doc_manager.generation.ollama import OllamaProvider
from doc_manager.generation.openai_provider import OpenAIProvider
from doc_manager.retrieval.service import ResolvedPath, SearchResult


def _evidence(text: str = "The Riverside office opens at 9:00 AM on weekdays.") -> SearchResult:
    return SearchResult(
        chunk_id="c1",
        content_object_id="o1",
        score=0.9,
        page_start=2,
        page_end=2,
        text=text,
        snippet=text[:40],
        availability="current",
        paths=[
            ResolvedPath(
                catalog_entry_id="e1",
                source_location_id="loc-1",
                display_path="/docs/hours.txt",
                state="indexed",
                is_primary=True,
            )
        ],
    )


class _FakeRetrieval:
    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results

    async def search(self, session: Any, **kwargs: Any) -> list[SearchResult]:
        return self._results


def _halves(text: str) -> tuple[str, str]:
    mid = len(text) // 2
    return text[:mid], text[mid:]


# --- adapter factories: script the same answer through each provider ---------


def _ollama(answer: str) -> tuple[OllamaProvider, Settings, bool]:
    a, b = _halves(answer)
    ndjson = (
        json.dumps({"message": {"content": a}, "done": False})
        + "\n"
        + json.dumps({"message": {"content": b}, "done": False})
        + "\n"
        + json.dumps(
            {
                "message": {"content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 20,
                "eval_count": 6,
            }
        )
        + "\n"
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=ndjson))
    provider = OllamaProvider(
        base_url="http://ollama",
        model="m",
        num_ctx=8192,
        max_output_tokens=512,
        transport=transport,
    )
    return provider, Settings(), False


def _openai(answer: str) -> tuple[OpenAIProvider, Settings, bool]:
    a, b = _halves(answer)

    async def create(**kwargs: Any) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            yield SimpleNamespace(type="response.output_text.delta", delta=a)
            yield SimpleNamespace(type="response.output_text.delta", delta=b)
            yield SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=20, output_tokens=6, total_tokens=26)
                ),
            )

        return gen()

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    provider = OpenAIProvider(
        model="gpt-test",
        api_key="sk-test",
        max_output_tokens=512,
        context_tokens=8192,
        timeout_seconds=30.0,
        client=client,  # type: ignore[arg-type]
    )
    settings = Settings(external_llm_enabled=True, openai_model="gpt-test")
    return provider, settings, True


ProviderFactory = Callable[[str], "tuple[Any, Settings, bool]"]

_FACTORIES = [
    pytest.param(_ollama, "local", id="ollama"),
    pytest.param(_openai, "external", id="openai"),
]


async def _run(factory: ProviderFactory, answer: str) -> AskResult:
    provider, settings, external = factory(answer)
    service = AskService(_FakeRetrieval([_evidence()]), settings)  # type: ignore[arg-type]
    if external:

        async def allow(session: Any, evidence: Any) -> list[str]:
            return ["allow"]

        setattr(service, "_source_policies", allow)  # noqa: B010
    request = AskRequest(
        question="When does the office open?",
        provider_id=provider.provider_id,
        external_processing_acknowledged=external,
    )
    return await service.ask(object(), request, provider)  # type: ignore[arg-type]


@pytest.mark.parametrize(("factory", "boundary"), _FACTORIES)
async def test_completed_answer_with_citation(factory: ProviderFactory, boundary: str) -> None:
    result = await _run(factory, "The office opens at 9am [E1].")
    assert result.status == "completed"
    assert result.answer == "The office opens at 9am [1]."
    assert [(c.citation_id, c.ordinal, c.chunk_id) for c in result.citations] == [("E1", 1, "c1")]
    assert result.citations[0].paths[0].display_path == "/docs/hours.txt"
    assert result.warnings == []
    assert result.data_boundary.classification == boundary


@pytest.mark.parametrize(("factory", "boundary"), _FACTORIES)
async def test_citation_ordinals_by_first_appearance(
    factory: ProviderFactory, boundary: str
) -> None:
    result = await _run(factory, "See [E1] and also [E1] again.")
    assert result.answer == "See [1] and also [1] again."
    assert len(result.citations) == 1


@pytest.mark.parametrize(("factory", "boundary"), _FACTORIES)
async def test_unknown_alias_dropped_with_warning(factory: ProviderFactory, boundary: str) -> None:
    result = await _run(factory, "Real [E1] and invented [E9].")
    assert result.answer == "Real [1] and invented ."
    assert [c.citation_id for c in result.citations] == ["E1"]
    assert "unknown_provider_citation_removed" in result.warnings


@pytest.mark.parametrize(("factory", "boundary"), _FACTORIES)
async def test_model_declared_insufficient(factory: ProviderFactory, boundary: str) -> None:
    result = await _run(factory, "INSUFFICIENT_EVIDENCE")
    assert result.status == "insufficient_evidence"
    assert result.answer is None
    assert result.invoked is True


@pytest.mark.parametrize(("factory", "boundary"), _FACTORIES)
async def test_usage_is_captured(factory: ProviderFactory, boundary: str) -> None:
    result = await _run(factory, "Answer [E1].")
    assert result.usage is not None
    assert result.usage.total_tokens == 26


# --- optional OpenAI live smoke (opt-in) ------------------------------------

_LIVE = os.getenv("DOCMAN_OPENAI_LIVE") == "1" and bool(os.getenv("OPENAI_API_KEY"))


@pytest.mark.skipif(not _LIVE, reason="set DOCMAN_OPENAI_LIVE=1 + OPENAI_API_KEY to run")
async def test_openai_live_smoke() -> None:  # pragma: no cover - opt-in only
    pytest.importorskip("openai")
    from doc_manager.generation.openai_provider import build_openai_provider

    settings = Settings(
        external_llm_enabled=True,
        openai_model=os.environ.get("DOCMAN_OPENAI_MODEL", "gpt-4o-mini"),
    )
    provider = build_openai_provider(settings)
    service = AskService(_FakeRetrieval([_evidence()]), settings)  # type: ignore[arg-type]

    async def allow(session: Any, evidence: Any) -> list[str]:
        return ["allow"]

    setattr(service, "_source_policies", allow)  # noqa: B010
    result = await service.ask(
        object(),  # type: ignore[arg-type]
        AskRequest(
            question="When does the office open? Cite [E1].",
            provider_id="openai",
            external_processing_acknowledged=True,
        ),
        provider,
    )
    assert result.status in ("completed", "insufficient_evidence")
    assert result.data_boundary.classification == "external"
    # Metadata never leaves the host.
    assert result.data_boundary.external_payload.paths_sent == 0
    assert result.data_boundary.external_payload.file_names_sent == 0
