"""Qdrant vector repository (TECHSTACK section 5.9, Phase 4.c).

Collection lifecycle bound to an embedding profile, idempotent deterministic point
upserts, filtered search, and the point-set accessor a consistency check needs.
Retrieval-only payload — paths/tags/source names are resolved from PostgreSQL at
query time, never stored here. Consumed by the ``index_file`` job and ``/search``.
"""

from doc_manager.vectors.errors import VectorStoreError, VectorStoreErrorCode
from doc_manager.vectors.point import VectorPoint, build_point, point_id
from doc_manager.vectors.repository import (
    QdrantRepository,
    SearchHit,
    build_qdrant_repository,
)

__all__ = [
    "QdrantRepository",
    "SearchHit",
    "VectorPoint",
    "VectorStoreError",
    "VectorStoreErrorCode",
    "build_point",
    "build_qdrant_repository",
    "point_id",
]
