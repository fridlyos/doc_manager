"""Deterministic page-aware chunking (TECHSTACK section 5.7, Phase 4.a).

Turns normalized page/section records into retrieval chunks with stable,
content-derived IDs. Pure library — no DB, no vectors, no filesystem; the
``index_file`` job wires it into embedding + Qdrant upsert in later Phase 4 steps.
"""

from doc_manager.chunking.chunker import Chunk, chunk_pages
from doc_manager.chunking.profile import (
    CHUNKING_VERSION,
    ChunkingProfile,
    chunk_id,
    chunking_profile_hash,
    default_chunking_profile,
)
from doc_manager.chunking.tokenizer import DEFAULT_TOKENIZER, Tokenizer, WhitespaceTokenizer

__all__ = [
    "CHUNKING_VERSION",
    "DEFAULT_TOKENIZER",
    "Chunk",
    "ChunkingProfile",
    "Tokenizer",
    "WhitespaceTokenizer",
    "chunk_id",
    "chunk_pages",
    "chunking_profile_hash",
    "default_chunking_profile",
]
