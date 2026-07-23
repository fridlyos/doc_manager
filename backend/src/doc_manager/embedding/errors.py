"""Embedding failures (TECHSTACK section 5.8).

These are configuration/consistency faults — an unknown model, a model that fails
to load, or vectors whose size does not match the active embedding profile. They
are distinct from a transient network hiccup; the index_file job maps them to a
permanent job error so a broken embedding config does not retry forever.
"""

from __future__ import annotations

from enum import StrEnum


class EmbeddingErrorCode(StrEnum):
    unknown_model = "unknown_model"
    model_load_failed = "model_load_failed"
    vector_size_mismatch = "vector_size_mismatch"


class EmbeddingError(Exception):
    """A permanent embedding configuration/consistency error."""

    def __init__(self, code: EmbeddingErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
