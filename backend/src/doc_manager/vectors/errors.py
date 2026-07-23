"""Vector-store failures (TECHSTACK section 5.9).

A collection-geometry mismatch is a permanent configuration/consistency fault:
the active embedding profile disagrees with an existing collection's vector size
or distance metric. Refusing it (rather than upserting) is what keeps incompatible
vectors out of one collection.
"""

from __future__ import annotations

from enum import StrEnum


class VectorStoreErrorCode(StrEnum):
    collection_mismatch = "collection_mismatch"


class VectorStoreError(Exception):
    """A permanent vector-store configuration/consistency error."""

    def __init__(self, code: VectorStoreErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
