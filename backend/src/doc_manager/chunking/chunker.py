"""Deterministic, page-aware chunking (TECHSTACK section 5.7).

Turns the ordered normalized pages of one document into retrieval chunks. Pure
library — no DB, no vectors, no filesystem; the ``index_file`` job (Phase 4
integration) reads pages from the artifact store and drives this, then persists
chunk rows and upserts vector points.

Algorithm — page boundaries are the preferred cut points so a chunk maps to as
few physical pages as possible (citation precision). Two adjustments handle the
extremes:

* **Small pages are packed.** Consecutive pages are greedily combined into one
  chunk while their cumulative token count stays within ``target_tokens``. The
  chunk records the spanned page range (``page_start``..``page_end``). This is
  the only case where a chunk crosses a page boundary, and it happens only when
  the pages are individually well under target.
* **Large pages are split.** A single page whose token count exceeds
  ``target_tokens`` is windowed into overlapping sub-chunks of ``target_tokens``
  advancing by ``target_tokens - overlap_tokens``. Every sub-chunk stays within
  that one page (``page_start == page_end``), so overlap never leaks across a
  page boundary.

Determinism: identical input pages + profile ⇒ identical chunk sequence (text,
boundaries, ordinals), hence identical ``chunk_id``s. That is what makes
re-indexing an idempotent upsert (Phase 4 exit criterion 1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from doc_manager.chunking.profile import ChunkingProfile
from doc_manager.chunking.tokenizer import DEFAULT_TOKENIZER, Tokenizer
from doc_manager.extraction.normalize import NormalizedPage, normalize_text


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrieval chunk. IDs are assigned later from content + profile."""

    #: 0-based ordinal within the document; dense and gap-free.
    index: int
    text: str
    token_count: int
    #: Physical page range this chunk covers (1-based, inclusive). ``None`` for
    #: formats without real pages (TXT/MD/CSV/log carry ``page_number=None``).
    page_start: int | None
    page_end: int | None
    #: SHA-256 of the chunk text; a stable content fingerprint for the chunk.
    text_hash: str


def chunk_pages(
    pages: Sequence[NormalizedPage],
    *,
    profile: ChunkingProfile,
    tokenizer: Tokenizer = DEFAULT_TOKENIZER,
) -> list[Chunk]:
    """Chunk ordered normalized pages under ``profile``.

    ``tokenizer`` must be the one named by ``profile.tokenizer_id`` — the profile
    hash records that identity so a mismatch would corrupt reuse keys.
    """
    target = profile.target_tokens
    overlap = profile.overlap_tokens
    builder = _ChunkBuilder(tokenizer)

    pending: list[tuple[NormalizedPage, list[str]]] = []
    pending_tokens = 0

    def flush() -> None:
        nonlocal pending, pending_tokens
        if not pending:
            return
        text = " ".join(token for _, tokens in pending for token in tokens)
        builder.emit(text, pending[0][0].page_number, pending[-1][0].page_number)
        pending = []
        pending_tokens = 0

    for page in pages:
        tokens = tokenizer.tokenize(page.text)
        if not tokens:
            continue  # blank page/section contributes nothing.
        if len(tokens) > target:
            # Oversized page: emit any packed small pages first, then split this
            # page alone into overlapping windows.
            flush()
            for window in _windows(tokens, target, overlap):
                builder.emit(" ".join(window), page.page_number, page.page_number)
            continue
        # Would adding this page overflow the pending pack? Close it first.
        if pending and pending_tokens + len(tokens) > target:
            flush()
        pending.append((page, tokens))
        pending_tokens += len(tokens)

    flush()
    return builder.chunks


class _ChunkBuilder:
    """Accumulates chunks, assigning dense ordinals and skipping empties."""

    def __init__(self, tokenizer: Tokenizer) -> None:
        self._tokenizer = tokenizer
        self.chunks: list[Chunk] = []

    def emit(self, text: str, page_start: int | None, page_end: int | None) -> None:
        # Re-normalize defensively; on already-normalized input this is a no-op.
        normalized = normalize_text(text)
        if not normalized:
            return
        self.chunks.append(
            Chunk(
                index=len(self.chunks),
                text=normalized,
                token_count=self._tokenizer.count(normalized),
                page_start=page_start,
                page_end=page_end,
                text_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            )
        )


def _windows(tokens: list[str], target: int, overlap: int) -> Iterator[list[str]]:
    """Overlapping windows of ``target`` tokens advancing by ``target - overlap``.

    The final window is emitted when it reaches the end even if shorter than
    ``target``; the loop never yields a duplicate tail. ``overlap < target`` is
    guaranteed by ``ChunkingProfile`` validation, so ``step >= 1``.
    """
    step = target - overlap
    n = len(tokens)
    start = 0
    while start < n:
        yield tokens[start : start + target]
        if start + target >= n:
            break
        start += step
