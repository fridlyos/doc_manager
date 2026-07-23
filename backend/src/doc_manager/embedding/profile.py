"""Embedding-profile identity (TECHSTACK section 5.8).

The *embedding profile* is everything that determines whether two vectors are
comparable: the model, its output vector size, the distance metric, whether
outputs are normalized, and the query/passage prefix scheme. Its hash names the
Qdrant collection, so an embedding-profile change routes to a *new* collection
instead of silently writing incompatible vectors into the active one (TECHSTACK
5.8: "An embedding-profile change creates a new collection or a controlled
rebuild. It never silently writes incompatible vectors into the active
collection.").

Pure and dependency-free: the FastEmbed lookups that populate a profile live in
``service.py`` so this module stays trivially testable.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

#: Bump when the meaning of a profile's fields changes (not on a model swap —
#: that is captured by ``model_name``/``vector_size``).
EMBEDDING_PROFILE_VERSION = "embed-1"

#: Qdrant distance metric for normalized embeddings.
DISTANCE_COSINE = "cosine"

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """Immutable identity of an embedding configuration."""

    model_name: str
    vector_size: int
    distance: str = DISTANCE_COSINE
    normalize: bool = True
    #: How query/passage prefixes are applied. ``"fastembed"`` means the library's
    #: model-specific ``query_embed``/``passage_embed`` prompts are used.
    prefix_scheme: str = "fastembed"
    version: str = EMBEDDING_PROFILE_VERSION

    def __post_init__(self) -> None:
        if self.vector_size <= 0:
            raise ValueError("vector_size must be positive")
        if not self.model_name:
            raise ValueError("model_name must not be empty")

    @property
    def hash(self) -> str:
        return embedding_profile_hash(
            model_name=self.model_name,
            vector_size=self.vector_size,
            distance=self.distance,
            normalize=self.normalize,
            prefix_scheme=self.prefix_scheme,
            version=self.version,
        )

    def collection_name(self, base: str) -> str:
        """Deterministic Qdrant collection name binding this profile.

        ``{base}__{model-slug}__{hash[:12]}`` — the slug aids humans, the hash
        makes it unique per profile so incompatible vectors can never share a
        collection.
        """
        slug = _SLUG.sub("-", self.model_name.lower()).strip("-")
        return f"{base}__{slug}__{self.hash[:12]}"

    def is_compatible_with(self, *, vector_size: int, distance: str) -> bool:
        """Whether an existing collection's geometry matches this profile.

        Used by the Qdrant repository (Phase 4.c) to validate — and refuse — an
        existing collection whose size/metric disagree with the active profile.
        """
        return self.vector_size == vector_size and self.distance == distance


def embedding_profile_hash(
    *,
    model_name: str,
    vector_size: int,
    distance: str,
    normalize: bool,
    prefix_scheme: str,
    version: str,
) -> str:
    canonical = json.dumps(
        {
            "model_name": model_name,
            "vector_size": vector_size,
            "distance": distance,
            "normalize": normalize,
            "prefix_scheme": prefix_scheme,
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
