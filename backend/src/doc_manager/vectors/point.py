"""Vector point identity and retrieval-only payload (TECHSTACK sections 5.7, 5.9).

A point's ID is derived deterministically from the content object, the chunking
profile, the embedding profile, and the chunk ordinal — so re-indexing the same
content under the same profiles reproduces the same IDs and the upsert is
idempotent (Phase 4 exit criterion 1). Folding the *embedding* profile into the
point ID (but not the chunk ID) means the same chunk embedded under two profiles
yields two distinct points that never collide.

The payload carries only what search needs to rank and stitch a citation:
identifiers, the page range, and the chunk text. It deliberately excludes paths,
filenames, tags, and source names — those are resolved from PostgreSQL at query
time so a moved file never produces a stale citation (TECHSTACK 5.9).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from doc_manager.chunking.chunker import Chunk

#: Fixed namespace for deterministic point UUIDv5s. Never change it.
_POINT_NAMESPACE = uuid.UUID("2c1e8a4d-7b93-4f16-a5c2-0d9e3f7b6a18")


def point_id(
    content_object_id: uuid.UUID | str,
    chunking_profile_hash: str,
    embedding_profile_hash: str,
    chunk_index: int,
) -> uuid.UUID:
    """Deterministic point UUIDv5 from content + both profiles + ordinal."""
    return uuid.uuid5(
        _POINT_NAMESPACE,
        f"{content_object_id}:{chunking_profile_hash}:{embedding_profile_hash}:{chunk_index}",
    )


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """A ready-to-upsert Qdrant point: deterministic id, vector, and payload."""

    id: uuid.UUID
    vector: list[float]
    payload: dict[str, object]


def build_point(
    *,
    content_object_id: uuid.UUID | str,
    chunk_id: uuid.UUID | str,
    chunk: Chunk,
    vector: list[float],
    chunking_profile_hash: str,
    embedding_profile_hash: str,
) -> VectorPoint:
    """Assemble a point for one chunk. Payload is retrieval-only."""
    pid = point_id(content_object_id, chunking_profile_hash, embedding_profile_hash, chunk.index)
    payload: dict[str, object] = {
        "content_object_id": str(content_object_id),
        "chunk_id": str(chunk_id),
        "chunk_index": chunk.index,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "text": chunk.text,
        "chunking_profile_hash": chunking_profile_hash,
        "embedding_profile_hash": embedding_profile_hash,
    }
    return VectorPoint(id=pid, vector=vector, payload=payload)
