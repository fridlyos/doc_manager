"""`/search` endpoint: envelope shape, validation, and provider-free operation.

A fake retrieval service is injected on ``app.state`` so these are unit tests —
no PostgreSQL, no Qdrant, and no embedding model. Retrieval logic itself is
covered by the PG+Qdrant integration test.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from doc_manager.core.config import Settings
from doc_manager.main import create_app
from doc_manager.retrieval import ResolvedPath, SearchFilters, SearchResult


class FakeRetrieval:
    """Records the last call and returns one canned hit."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        session: Any,
        *,
        query: str,
        filters: SearchFilters | None,
        top_k: int,
        score_threshold: float | None,
    ) -> list[SearchResult]:
        self.calls.append(
            {"query": query, "filters": filters, "top_k": top_k, "threshold": score_threshold}
        )
        return [
            SearchResult(
                chunk_id="11111111-1111-5111-8111-111111111111",
                content_object_id="22222222-2222-4222-8222-222222222222",
                score=0.87,
                page_start=4,
                page_end=4,
                text="the renewal clause covers december terms in full",
                snippet="the renewal clause covers december",
                availability="current",
                paths=[
                    ResolvedPath(
                        catalog_entry_id="33333333-3333-4333-8333-333333333333",
                        source_location_id="44444444-4444-4444-8444-444444444444",
                        display_path="/sources/docs/contract.txt",
                        state="indexed",
                        is_primary=True,
                    )
                ],
            )
        ]


@pytest.fixture
def fake() -> FakeRetrieval:
    return FakeRetrieval()


@pytest.fixture
def client(fake: FakeRetrieval) -> Iterator[TestClient]:
    settings = Settings(env="test", search_top_k=9)
    app = create_app(settings)
    app.state.retrieval_service = fake  # get_retrieval_service returns this, no model load.
    with TestClient(app) as test_client:
        yield test_client


def test_search_returns_result_envelope(client: TestClient, fake: FakeRetrieval) -> None:
    resp = client.post("/api/v1/search", json={"query": "when does it renew?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["api_version"] == "1"
    data = body["data"]
    assert data["result_count"] == 1
    hit = data["results"][0]
    assert hit["similarity_score"] == 0.87
    assert hit["page_start"] == 4
    assert hit["availability"] == "current"
    assert hit["paths"][0]["display_path"] == "/sources/docs/contract.txt"
    assert hit["paths"][0]["is_primary"] is True
    # No generation provider fields leak into a search response.
    assert "provider" not in data
    assert "answer" not in data


def test_default_top_k_from_settings(client: TestClient, fake: FakeRetrieval) -> None:
    client.post("/api/v1/search", json={"query": "hello"})
    assert fake.calls[-1]["top_k"] == 9  # settings.search_top_k


def test_explicit_retrieval_params_forwarded(client: TestClient, fake: FakeRetrieval) -> None:
    client.post(
        "/api/v1/search",
        json={"query": "hello", "retrieval": {"top_k": 3, "score_threshold": 0.5}},
    )
    assert fake.calls[-1]["top_k"] == 3
    assert fake.calls[-1]["threshold"] == 0.5


def test_filters_forwarded(client: TestClient, fake: FakeRetrieval) -> None:
    client.post(
        "/api/v1/search",
        json={"query": "hello", "filters": {"extensions": ["pdf", "md"]}},
    )
    assert fake.calls[-1]["filters"].extensions == ["pdf", "md"]


def test_blank_query_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/search", json={"query": "   "})
    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"


def test_empty_query_rejected(client: TestClient) -> None:
    assert client.post("/api/v1/search", json={"query": ""}).status_code == 422


def test_empty_filter_array_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/search", json={"query": "x", "filters": {"extensions": []}})
    assert resp.status_code == 422


def test_top_k_bounds_enforced(client: TestClient) -> None:
    assert (
        client.post("/api/v1/search", json={"query": "x", "retrieval": {"top_k": 0}}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/search", json={"query": "x", "retrieval": {"top_k": 101}}).status_code
        == 422
    )


def test_unknown_field_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/search", json={"query": "x", "bogus": 1})
    assert resp.status_code == 422
