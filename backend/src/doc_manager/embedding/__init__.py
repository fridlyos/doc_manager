"""FastEmbed embedding adapter + embedding-profile identity (TECHSTACK 5.8).

Loads a local FastEmbed model once per process and embeds document chunks and
queries through separate, prefix-correct calls. The embedding profile names the
Qdrant collection so incompatible vectors can never share one. Pure profile logic
lives in ``profile.py``; the model I/O lives in ``service.py`` (FastEmbed imported
lazily). Consumed by the Qdrant repository and ``/search`` in later Phase 4 steps.
"""

from doc_manager.embedding.errors import EmbeddingError, EmbeddingErrorCode
from doc_manager.embedding.profile import (
    DISTANCE_COSINE,
    EMBEDDING_PROFILE_VERSION,
    EmbeddingProfile,
    embedding_profile_hash,
)
from doc_manager.embedding.service import (
    Embedder,
    EmbeddingService,
    build_embedding_service,
    load_fastembed,
    resolve_embedding_profile,
)

__all__ = [
    "DISTANCE_COSINE",
    "EMBEDDING_PROFILE_VERSION",
    "Embedder",
    "EmbeddingError",
    "EmbeddingErrorCode",
    "EmbeddingProfile",
    "EmbeddingService",
    "build_embedding_service",
    "embedding_profile_hash",
    "load_fastembed",
    "resolve_embedding_profile",
]
