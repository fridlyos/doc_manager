"""FastEmbed embedding service (TECHSTACK section 5.8).

Loads a FastEmbed model once per process and produces vectors for document
chunks and for queries through *separate* calls, so the model's own
query/passage prefixes are applied correctly. Batches document work and validates
that every produced vector matches the active embedding profile's size — a wrong
model or a corrupted output fails closed rather than polluting the collection.

FastEmbed is imported lazily inside the loader so importing this module (and the
unit tests, which inject a fake embedder) never pays the heavy onnxruntime import
or triggers a model download.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import lru_cache
from typing import Any, Protocol, cast, runtime_checkable

from doc_manager.core.config import Settings
from doc_manager.core.logging import get_logger
from doc_manager.embedding.errors import EmbeddingError, EmbeddingErrorCode
from doc_manager.embedding.profile import DISTANCE_COSINE, EmbeddingProfile

log = get_logger("doc_manager.embedding")


@runtime_checkable
class Embedder(Protocol):
    """The subset of FastEmbed's ``TextEmbedding`` the service depends on."""

    def passage_embed(self, texts: Iterable[str], **kwargs: Any) -> Iterable[Any]:
        """Embed document/passage texts (model passage prefix applied)."""
        ...

    def query_embed(self, query: str | Iterable[str], **kwargs: Any) -> Iterable[Any]:
        """Embed a query (model query prefix applied)."""
        ...


class EmbeddingService:
    """Embeds chunks and queries under a fixed :class:`EmbeddingProfile`."""

    def __init__(
        self, embedder: Embedder, profile: EmbeddingProfile, *, batch_size: int = 256
    ) -> None:
        self._embedder = embedder
        self._profile = profile
        self._batch_size = batch_size

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    @property
    def vector_size(self) -> int:
        return self._profile.vector_size

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed chunk texts, preserving order. Empty input → empty output."""
        if not texts:
            return []
        raw = list(self._embedder.passage_embed(texts, batch_size=self._batch_size))
        if len(raw) != len(texts):
            raise EmbeddingError(
                EmbeddingErrorCode.vector_size_mismatch,
                f"embedder returned {len(raw)} vectors for {len(texts)} documents",
            )
        return [self._as_vector(vec) for vec in raw]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        raw = list(self._embedder.query_embed(text))
        if len(raw) != 1:
            raise EmbeddingError(
                EmbeddingErrorCode.vector_size_mismatch,
                f"query embedding returned {len(raw)} vectors, expected 1",
            )
        return self._as_vector(raw[0])

    def _as_vector(self, vec: Any) -> list[float]:
        values = vec.tolist() if hasattr(vec, "tolist") else list(vec)
        if len(values) != self._profile.vector_size:
            raise EmbeddingError(
                EmbeddingErrorCode.vector_size_mismatch,
                f"expected {self._profile.vector_size}-d vectors from "
                f"{self._profile.model_name}, got {len(values)}",
            )
        return [float(v) for v in values]


def resolve_embedding_profile(settings: Settings) -> EmbeddingProfile:
    """Build the active profile, reading the model's vector size from FastEmbed."""
    model_name = settings.embedding_model
    return EmbeddingProfile(
        model_name=model_name,
        vector_size=_embedding_dim(model_name),
        distance=DISTANCE_COSINE,
    )


def build_embedding_service(settings: Settings) -> EmbeddingService:
    """Wire the cached FastEmbed model to the active profile."""
    profile = resolve_embedding_profile(settings)
    embedder = load_fastembed(profile.model_name)
    return EmbeddingService(embedder, profile, batch_size=settings.embedding_batch_size)


def _embedding_dim(model_name: str) -> int:
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:  # pragma: no cover - dep is installed in the image
        raise EmbeddingError(
            EmbeddingErrorCode.model_load_failed, "fastembed is not installed"
        ) from exc
    try:
        return int(TextEmbedding.get_embedding_size(model_name))
    except Exception as exc:
        raise EmbeddingError(
            EmbeddingErrorCode.unknown_model, f"unknown embedding model: {model_name}"
        ) from exc


@lru_cache(maxsize=4)
def load_fastembed(model_name: str) -> Embedder:
    """Load (and cache per process) a FastEmbed model.

    The first call downloads/loads the ONNX model; subsequent calls reuse it, so a
    worker pays the cost once (TECHSTACK 5.8: "Loads the configured FastEmbed model
    once per worker process.").
    """
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:  # pragma: no cover - dep is installed in the image
        raise EmbeddingError(
            EmbeddingErrorCode.model_load_failed, "fastembed is not installed"
        ) from exc
    try:
        model = TextEmbedding(model_name=model_name)
    except Exception as exc:
        raise EmbeddingError(
            EmbeddingErrorCode.model_load_failed, f"failed to load {model_name}: {exc}"
        ) from exc
    log.info("fastembed_model_loaded", model=model_name)
    return cast(Embedder, model)
