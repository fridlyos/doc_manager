"""Chunking-profile identity and deterministic chunk IDs (TECHSTACK section 5.7).

The *chunking profile* is the set of knobs that determine how normalized pages
become chunks: the algorithm version, target/overlap token budgets, and the
tokenizer identity. Its hash participates in a chunk's reuse key so that changing
any knob produces a distinct chunking profile — old chunks are never silently
mixed with chunks produced by different logic.

Chunk IDs are derived deterministically from ``(content_object_id,
chunking_profile_hash, chunk_index)`` via UUIDv5. Because a content object is
already content-addressed (identical structured content → one content object),
re-running the chunker on the same content under the same profile yields byte-for
-byte identical IDs. That is what makes re-indexing an idempotent upsert with no
duplicate chunks or vector points (Phase 4 exit criterion 1).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from doc_manager.chunking.tokenizer import DEFAULT_TOKENIZER

#: Bump when the chunking algorithm in ``chunker.py`` changes in a way that would
#: alter chunk boundaries for unchanged input.
CHUNKING_VERSION = "chunk-1"

#: Fixed namespace for deterministic chunk UUIDv5s. Never change it: doing so
#: would repoint every chunk ID and orphan existing vector points.
_CHUNK_NAMESPACE = uuid.UUID("6f9d8c2a-1e7b-4c3a-9a2e-8b5d4f1c0e77")


@dataclass(frozen=True, slots=True)
class ChunkingProfile:
    """Immutable set of chunking knobs. ``hash`` is its stable identity."""

    target_tokens: int
    overlap_tokens: int
    tokenizer_id: str
    version: str = CHUNKING_VERSION

    def __post_init__(self) -> None:
        if self.target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if self.overlap_tokens < 0:
            raise ValueError("overlap_tokens must not be negative")
        if self.overlap_tokens >= self.target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")

    @property
    def hash(self) -> str:
        return chunking_profile_hash(
            version=self.version,
            target_tokens=self.target_tokens,
            overlap_tokens=self.overlap_tokens,
            tokenizer_id=self.tokenizer_id,
        )


def default_chunking_profile(
    *, target_tokens: int = 750, overlap_tokens: int = 100
) -> ChunkingProfile:
    """The active profile: TECHSTACK defaults over the pure whitespace tokenizer.

    Token budgets come from ``Settings.chunk_target_tokens`` /
    ``chunk_overlap_tokens`` at the call site; the defaults here mirror the spec.
    """
    return ChunkingProfile(
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
        tokenizer_id=DEFAULT_TOKENIZER.id,
    )


def chunking_profile_hash(
    *, version: str, target_tokens: int, overlap_tokens: int, tokenizer_id: str
) -> str:
    canonical = json.dumps(
        {
            "version": version,
            "target_tokens": target_tokens,
            "overlap_tokens": overlap_tokens,
            "tokenizer_id": tokenizer_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def chunk_id(content_object_id: uuid.UUID | str, profile_hash: str, chunk_index: int) -> uuid.UUID:
    """Deterministic chunk UUIDv5 from content + profile + ordinal.

    Stable across runs and machines, so re-chunking the same content object under
    the same chunking profile reproduces the same IDs (idempotent upsert).
    """
    return uuid.uuid5(_CHUNK_NAMESPACE, f"{content_object_id}:{profile_hash}:{chunk_index}")
