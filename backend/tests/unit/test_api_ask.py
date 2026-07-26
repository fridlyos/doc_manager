"""Ask + provider endpoints (Phase 5.f): result envelope, SSE, discovery.

Unit tests — a fake AskService and a fake registry are injected on app.state, so
no DB / provider / model is touched. The SSE ordering + single terminal event and
the §8.2 result shape are asserted against the real routes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from doc_manager.core.config import Settings
from doc_manager.generation.ask import (
    AnswerDelta,
    AskResult,
    AskResultEvent,
    AskStarted,
    CitationResolved,
    GenerationStarted,
    RetrievalCompleted,
)
from doc_manager.generation.base import DataBoundary, ProviderReadiness
from doc_manager.generation.boundary import local_boundary
from doc_manager.generation.errors import GenerationError, GenerationErrorCode
from doc_manager.generation.rag import Citation
from doc_manager.main import create_app


class _ProviderStub:
    def __init__(self, provider_id: str, boundary: DataBoundary, ready: bool = True) -> None:
        self.provider_id = provider_id
        self.data_boundary = boundary
        self._ready = ready

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=self._ready, detail="", model_id="m")


class FakeRegistry:
    def __init__(self, eligible: set[str]) -> None:
        self._providers = {
            "ollama": _ProviderStub("ollama", DataBoundary.local),
            "openai": _ProviderStub("openai", DataBoundary.external),
        }
        self._eligible = eligible

    def require_eligible(self, settings: Any, provider_id: str) -> Any:
        if provider_id not in self._providers:
            raise GenerationError(GenerationErrorCode.unknown_provider, "no such provider")
        if provider_id not in self._eligible:
            raise GenerationError(
                GenerationErrorCode.provider_unavailable, "not enabled", retryable=False
            )
        return self._providers[provider_id]

    def get(self, provider_id: str) -> Any:
        if provider_id not in self._providers:
            raise GenerationError(GenerationErrorCode.unknown_provider, "no such provider")
        return self._providers[provider_id]

    def all(self) -> list[Any]:
        return list(self._providers.values())

    def eligible_ids(self, settings: Any) -> list[str]:
        return [p for p in self._providers if p in self._eligible]

    def is_eligible(self, settings: Any, provider: Any) -> bool:
        return provider.provider_id in self._eligible


def _result() -> AskResult:
    return AskResult(
        id="ask-1",
        status="completed",
        provider_id="ollama",
        model_id="llama3.1:8b",
        data_boundary=local_boundary(),
        invoked=True,
        candidate_count=5,
        selected_count=2,
        sufficient=True,
        answer="It renews in December [1].",
        citations=[
            Citation(
                citation_id="E1",
                ordinal=1,
                chunk_id="c1",
                page_start=4,
                page_end=4,
                snippet="renews in december",
                availability="current",
                similarity_score=0.81,
                paths=[],
            )
        ],
        finish_reason="stop",
        retrieval_ms=10,
        generation_ms=100,
        total_ms=110,
    )


class FakeAsk:
    def __init__(self, result: AskResult, events: list[Any] | None = None) -> None:
        self._result = result
        self._events = events or []

    async def ask(self, session: Any, request: Any, provider: Any) -> AskResult:
        return self._result

    async def ask_stream(self, session: Any, request: Any, provider: Any) -> AsyncIterator[Any]:
        for event in self._events:
            yield event


def _stream_events() -> list[Any]:
    r = _result()
    return [
        AskStarted("ollama", "llama3.1:8b", "local"),
        RetrievalCompleted(5, 2, True),
        GenerationStarted("ollama", "llama3.1:8b", "local"),
        AnswerDelta("It renews "),
        AnswerDelta("in December [E1]."),
        CitationResolved(r.citations[0]),
        AskResultEvent(r),
    ]


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(Settings(env="test"))
    app.state.provider_registry = FakeRegistry(eligible={"ollama"})
    app.state.ask_service = FakeAsk(_result(), _stream_events())
    with TestClient(app) as test_client:
        yield test_client


def test_ask_returns_result_envelope(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ask", json={"question": "when does it renew?", "provider_id": "ollama"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "completed"
    assert data["answer"] == "It renews in December [1]."
    assert data["provider"]["data_boundary"] == "local"
    assert data["citations"][0]["ordinal"] == 1
    assert data["data_boundary"]["external_payload"]["paths_sent"] == 0


def test_ask_unknown_provider_is_404(client: TestClient) -> None:
    resp = client.post("/api/v1/ask", json={"question": "x", "provider_id": "nope"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "unknown_provider"


def test_ask_ineligible_provider_is_503(client: TestClient) -> None:
    resp = client.post("/api/v1/ask", json={"question": "x", "provider_id": "openai"})
    assert resp.status_code == 503
    assert resp.json()["code"] == "provider_unavailable"


def test_ask_blank_question_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/ask", json={"question": "   ", "provider_id": "ollama"})
    assert resp.status_code == 422


def _parse_sse(text: str) -> list[tuple[int, str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        lines = block.splitlines()
        eid = int(next(x.split("id:", 1)[1].strip() for x in lines if x.startswith("id:")))
        name = next(x.split("event:", 1)[1].strip() for x in lines if x.startswith("event:"))
        data = json.loads(
            next(x.split("data:", 1)[1].strip() for x in lines if x.startswith("data:"))
        )
        events.append((eid, name, data))
    return events


def test_ask_stream_emits_ordered_events_with_single_terminal(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/ask/stream", json={"question": "when does it renew?", "provider_id": "ollama"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    names = [n for _, n, _ in events]
    assert names == [
        "ask.started",
        "retrieval.completed",
        "generation.started",
        "answer.delta",
        "answer.delta",
        "citation.resolved",
        "ask.result",
    ]
    # Incrementing ids + sequence, common fields present.
    ids = [i for i, _, _ in events]
    assert ids == list(range(1, len(events) + 1))
    assert events[0][2]["stream_version"] == "1.0"
    assert names.count("ask.result") == 1
    # Terminal carries the full §8.2 result.
    assert events[-1][2]["data"]["answer"] == "It renews in December [1]."


def test_ask_stream_provider_error_is_ordinary_problem(client: TestClient) -> None:
    # Pre-stream provider ineligibility is a normal Problem, not an SSE error.
    resp = client.post("/api/v1/ask/stream", json={"question": "x", "provider_id": "openai"})
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_list_providers(client: TestClient) -> None:
    data = client.get("/api/v1/system/providers").json()["data"]
    by_id = {p["provider_id"]: p for p in data}
    assert by_id["ollama"]["eligible"] is True
    assert by_id["openai"]["eligible"] is False
    assert by_id["ollama"]["data_boundary"] == "local"


def test_provider_test_probe(client: TestClient) -> None:
    data = client.post("/api/v1/system/providers/ollama/test").json()["data"]
    assert data["ready"] is True
    assert data["model_id"] == "m"

    missing = client.post("/api/v1/system/providers/nope/test")
    assert missing.status_code == 404
