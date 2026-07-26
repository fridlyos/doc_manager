"""remove_stale_vectors collection retirement (Phase 6.b).

Runs against qdrant-client in-memory mode: only same-namespace collections are
dropped, the active one is kept, and unrelated collections are untouched.
"""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient

from doc_manager.embedding.profile import EmbeddingProfile
from doc_manager.jobs.handlers.cleanup import drop_stale_collections
from doc_manager.vectors import QdrantRepository


async def _create(client: AsyncQdrantClient, name: str, dim: int = 4) -> None:
    repo = QdrantRepository(client, collection=name)
    await repo.ensure_collection(EmbeddingProfile(model_name="m", vector_size=dim))


async def test_drops_only_stale_namespace_collections() -> None:
    client = AsyncQdrantClient(location=":memory:")
    active = "doc_chunks__bge__aaaaaaaaaaaa"
    stale = "doc_chunks__bge__bbbbbbbbbbbb"
    other = "unrelated_collection"
    for name in (active, stale, other):
        await _create(client, name)

    repo = QdrantRepository(client, collection=active)
    dropped = await drop_stale_collections(repo, active=active, base_prefix="doc_chunks__")

    assert dropped == [stale]
    remaining = set(await repo.list_collection_names())
    assert active in remaining
    assert other in remaining  # never touched — different namespace
    assert stale not in remaining


async def test_no_stale_collections_is_noop() -> None:
    client = AsyncQdrantClient(location=":memory:")
    active = "doc_chunks__bge__aaaaaaaaaaaa"
    await _create(client, active)
    repo = QdrantRepository(client, collection=active)
    dropped = await drop_stale_collections(repo, active=active, base_prefix="doc_chunks__")
    assert dropped == []
    assert await repo.list_collection_names() == [active]
