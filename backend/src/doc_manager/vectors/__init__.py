"""Qdrant vector repository (TECHSTACK section 5.9, Phase 4.c).

Collection lifecycle bound to an embedding profile, idempotent deterministic point
upserts, filtered search, and the point-set accessor a consistency check needs.
Retrieval-only payload — paths/tags/source names are resolved from PostgreSQL at
query time, never stored here. Consumed by the ``index_file`` job and ``/search``.
"""

from doc_manager.vectors.errors import VectorStoreError, VectorStoreErrorCode
from doc_manager.vectors.names import (
    NameIndex,
    NamePoint,
    build_name_index,
    file_name_text,
    name_hash,
)
from doc_manager.vectors.point import VectorPoint, build_point, point_id
from doc_manager.vectors.repository import (
    QdrantRepository,
    SearchHit,
    build_qdrant_repository,
)

__all__ = [
    "NameIndex",
    "NamePoint",
    "QdrantRepository",
    "SearchHit",
    "VectorPoint",
    "VectorStoreError",
    "VectorStoreErrorCode",
    "build_name_index",
    "build_point",
    "build_qdrant_repository",
    "file_name_text",
    "name_hash",
    "point_id",
]
