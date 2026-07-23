"""Embedding service + profile: prefix routing, validation, profile identity.

The FastEmbed model is never loaded here — a fake embedder stands in — so these
stay fast and offline. One metadata test exercises the real FastEmbed registry
(no model download) and skips if the dep is absent.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from doc_manager.embedding import (
    EMBEDDING_PROFILE_VERSION,
    EmbeddingError,
    EmbeddingProfile,
    EmbeddingService,
    embedding_profile_hash,
)


class FakeEmbedder:
    """Records which prefix path was used and returns fixed-size vectors."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.passage_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def passage_embed(self, texts: Iterable[str], **kwargs: Any) -> Iterable[Any]:
        items = list(texts)
        self.passage_calls.append(items)
        # Deterministic per-text vector so order is checkable.
        for i, _ in enumerate(items):
            yield [float(i)] * self.dim

    def query_embed(self, query: str | Iterable[str], **kwargs: Any) -> Iterable[Any]:
        assert isinstance(query, str)
        self.query_calls.append(query)
        yield [0.5] * self.dim


def _profile(dim: int = 4) -> EmbeddingProfile:
    return EmbeddingProfile(model_name="fake/model", vector_size=dim)


def test_embed_documents_uses_passage_path_and_preserves_order() -> None:
    emb = FakeEmbedder(dim=4)
    svc = EmbeddingService(emb, _profile(4))
    vectors = svc.embed_documents(["a", "b", "c"])
    assert vectors == [[0.0] * 4, [1.0] * 4, [2.0] * 4]
    assert emb.passage_calls == [["a", "b", "c"]]
    assert emb.query_calls == []  # documents never use the query prefix.


def test_embed_query_uses_query_path() -> None:
    emb = FakeEmbedder(dim=4)
    svc = EmbeddingService(emb, _profile(4))
    vec = svc.embed_query("when does it renew?")
    assert vec == [0.5] * 4
    assert emb.query_calls == ["when does it renew?"]
    assert emb.passage_calls == []


def test_empty_documents_returns_empty() -> None:
    emb = FakeEmbedder()
    svc = EmbeddingService(emb, _profile())
    assert svc.embed_documents([]) == []
    assert emb.passage_calls == []


def test_vector_size_mismatch_is_rejected() -> None:
    emb = FakeEmbedder(dim=8)  # produces 8-d, profile expects 4-d.
    svc = EmbeddingService(emb, _profile(4))
    with pytest.raises(EmbeddingError) as exc:
        svc.embed_documents(["a"])
    assert exc.value.code.value == "vector_size_mismatch"


def test_batch_size_is_forwarded() -> None:
    seen: dict[str, Any] = {}

    class Recorder(FakeEmbedder):
        def passage_embed(self, texts: Iterable[str], **kwargs: Any) -> Iterable[Any]:
            seen["batch_size"] = kwargs.get("batch_size")
            return super().passage_embed(texts, **kwargs)

    svc = EmbeddingService(Recorder(4), _profile(4), batch_size=32)
    svc.embed_documents(["a"])
    assert seen["batch_size"] == 32


def test_profile_hash_and_collection_name() -> None:
    p = _profile(384)
    assert p.hash == embedding_profile_hash(
        model_name="fake/model",
        vector_size=384,
        distance="cosine",
        normalize=True,
        prefix_scheme="fastembed",
        version=EMBEDDING_PROFILE_VERSION,
    )
    name = p.collection_name("doc_chunks")
    assert name.startswith("doc_chunks__fake-model__")
    assert name.endswith(p.hash[:12])


def test_profile_hash_sensitive_to_every_field() -> None:
    base = {
        "model_name": "m",
        "vector_size": 384,
        "distance": "cosine",
        "normalize": True,
        "prefix_scheme": "fastembed",
        "version": EMBEDDING_PROFILE_VERSION,
    }
    h = embedding_profile_hash(**base)
    assert h != embedding_profile_hash(**{**base, "model_name": "m2"})
    assert h != embedding_profile_hash(**{**base, "vector_size": 512})
    assert h != embedding_profile_hash(**{**base, "distance": "dot"})
    assert h != embedding_profile_hash(**{**base, "normalize": False})
    assert h != embedding_profile_hash(**{**base, "prefix_scheme": "manual"})
    assert h != embedding_profile_hash(**{**base, "version": "embed-2"})


def test_is_compatible_with() -> None:
    p = _profile(384)
    assert p.is_compatible_with(vector_size=384, distance="cosine")
    assert not p.is_compatible_with(vector_size=512, distance="cosine")
    assert not p.is_compatible_with(vector_size=384, distance="dot")


def test_profile_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        EmbeddingProfile(model_name="m", vector_size=0)
    with pytest.raises(ValueError):
        EmbeddingProfile(model_name="", vector_size=4)


def test_resolve_dim_from_real_registry() -> None:
    pytest.importorskip("fastembed")
    from doc_manager.core.config import Settings
    from doc_manager.embedding.service import resolve_embedding_profile

    profile = resolve_embedding_profile(Settings(embedding_model="BAAI/bge-small-en-v1.5"))
    assert profile.vector_size == 384
    assert profile.distance == "cosine"


def test_resolve_unknown_model_raises() -> None:
    pytest.importorskip("fastembed")
    from doc_manager.core.config import Settings
    from doc_manager.embedding.service import resolve_embedding_profile

    with pytest.raises(EmbeddingError) as exc:
        resolve_embedding_profile(Settings(embedding_model="not/a-real-model-xyz"))
    assert exc.value.code.value == "unknown_model"
