"""Qdrant repository: lifecycle, idempotent upsert, filtered search, consistency.

Runs against qdrant-client's in-memory local mode (no server), so these are unit
tests: they exercise the real client API surface without a network dependency.
"""

from __future__ import annotations

import uuid

import pytest
from qdrant_client import AsyncQdrantClient

from doc_manager.chunking.chunker import Chunk
from doc_manager.embedding.profile import EmbeddingProfile
from doc_manager.vectors import (
    QdrantRepository,
    VectorStoreError,
    build_point,
    point_id,
)
from doc_manager.vectors.point import VectorPoint


def _profile(dim: int = 4, distance: str = "cosine") -> EmbeddingProfile:
    return EmbeddingProfile(model_name="fake/model", vector_size=dim, distance=distance)


async def _repo(collection: str = "chunks") -> QdrantRepository:
    return QdrantRepository(AsyncQdrantClient(location=":memory:"), collection=collection)


def _chunk(index: int, text: str = "some text") -> Chunk:
    return Chunk(
        index=index,
        text=text,
        token_count=2,
        page_start=index + 1,
        page_end=index + 1,
        text_hash="h" * 64,
    )


def _point(repo_content: uuid.UUID, chunk: Chunk, vector: list[float]) -> VectorPoint:
    return build_point(
        content_object_id=repo_content,
        chunk_id=uuid.uuid4(),
        chunk=chunk,
        vector=vector,
        chunking_profile_hash="cp",
        embedding_profile_hash="ep",
    )


async def test_ensure_collection_creates_when_absent() -> None:
    repo = await _repo()
    await repo.ensure_collection(_profile(4))
    # Idempotent: a second call validates the existing collection, no error.
    await repo.ensure_collection(_profile(4))


async def test_ensure_collection_refuses_size_mismatch() -> None:
    repo = await _repo()
    await repo.ensure_collection(_profile(4))
    with pytest.raises(VectorStoreError) as exc:
        await repo.ensure_collection(_profile(8))
    assert exc.value.code.value == "collection_mismatch"


async def test_ensure_collection_refuses_distance_mismatch() -> None:
    repo = await _repo()
    await repo.ensure_collection(_profile(4, distance="cosine"))
    with pytest.raises(VectorStoreError):
        await repo.ensure_collection(_profile(4, distance="dot"))


async def test_upsert_is_idempotent() -> None:
    repo = await _repo()
    content = uuid.uuid4()
    await repo.ensure_collection(_profile(4))
    points = [_point(content, _chunk(0), [1.0, 0.0, 0.0, 0.0])]
    assert await repo.upsert_points(points) == 1
    # Re-upserting the same deterministic points must not create duplicates.
    await repo.upsert_points(points)
    assert await repo.count_for_content(content) == 1


async def test_empty_upsert_is_noop() -> None:
    repo = await _repo()
    await repo.ensure_collection(_profile(4))
    assert await repo.upsert_points([]) == 0


async def test_search_returns_ranked_hits_with_threshold() -> None:
    repo = await _repo()
    content = uuid.uuid4()
    await repo.ensure_collection(_profile(4))
    await repo.upsert_points(
        [
            _point(content, _chunk(0, "renewal clause"), [1.0, 0.0, 0.0, 0.0]),
            _point(content, _chunk(1, "about cats"), [0.0, 1.0, 0.0, 0.0]),
        ]
    )
    hits = await repo.search([1.0, 0.0, 0.0, 0.0], top_k=5)
    assert hits[0].text == "renewal clause"
    assert hits[0].score > hits[-1].score
    # A high threshold drops the orthogonal (score ~0) hit.
    strict = await repo.search([1.0, 0.0, 0.0, 0.0], top_k=5, score_threshold=0.5)
    assert [h.text for h in strict] == ["renewal clause"]


async def test_search_filters_by_content_object() -> None:
    repo = await _repo()
    a, b = uuid.uuid4(), uuid.uuid4()
    await repo.ensure_collection(_profile(4))
    await repo.upsert_points([_point(a, _chunk(0, "from A"), [1.0, 0.0, 0.0, 0.0])])
    await repo.upsert_points([_point(b, _chunk(0, "from B"), [1.0, 0.0, 0.0, 0.0])])
    hits = await repo.search([1.0, 0.0, 0.0, 0.0], top_k=5, content_object_ids=[a])
    assert {h.content_object_id for h in hits} == {str(a)}
    # An empty allow-set short-circuits to no results (no query issued).
    assert await repo.search([1.0, 0.0, 0.0, 0.0], top_k=5, content_object_ids=[]) == []


async def test_delete_for_content_removes_points() -> None:
    repo = await _repo()
    content = uuid.uuid4()
    await repo.ensure_collection(_profile(4))
    await repo.upsert_points([_point(content, _chunk(0), [1.0, 0.0, 0.0, 0.0])])
    await repo.delete_for_content(content)
    assert await repo.count_for_content(content) == 0


async def test_point_ids_for_content_matches_deterministic_ids() -> None:
    repo = await _repo()
    content = uuid.uuid4()
    await repo.ensure_collection(_profile(4))
    chunks = [_chunk(i) for i in range(3)]
    await repo.upsert_points([_point(content, c, [1.0, 0.0, 0.0, 0.0]) for c in chunks])
    stored = await repo.point_ids_for_content(content)
    expected = {str(point_id(content, "cp", "ep", c.index)) for c in chunks}
    assert stored == expected


def test_point_id_folds_both_profiles() -> None:
    content = uuid.uuid4()
    base = point_id(content, "cp", "ep", 0)
    assert base == point_id(content, "cp", "ep", 0)
    assert base != point_id(content, "cp2", "ep", 0)  # chunking profile
    assert base != point_id(content, "cp", "ep2", 0)  # embedding profile
    assert base != point_id(content, "cp", "ep", 1)  # index
    assert base != point_id(uuid.uuid4(), "cp", "ep", 0)  # content


def test_payload_is_retrieval_only() -> None:
    content = uuid.uuid4()
    p = build_point(
        content_object_id=content,
        chunk_id=uuid.uuid4(),
        chunk=_chunk(0),
        vector=[1.0, 0.0, 0.0, 0.0],
        chunking_profile_hash="cp",
        embedding_profile_hash="ep",
    )
    forbidden = {"display_path", "path", "scan_root", "file_name", "tags", "source_location_id"}
    assert forbidden.isdisjoint(p.payload)
    assert set(p.payload) == {
        "content_object_id",
        "chunk_id",
        "chunk_index",
        "page_start",
        "page_end",
        "text",
        "chunking_profile_hash",
        "embedding_profile_hash",
    }
