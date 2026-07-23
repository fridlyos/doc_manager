"""Page-aware chunking: determinism, page-boundary respect, overlap, profiles."""

from __future__ import annotations

import uuid

from doc_manager.chunking import (
    CHUNKING_VERSION,
    Chunk,
    ChunkingProfile,
    chunk_id,
    chunk_pages,
    chunking_profile_hash,
    default_chunking_profile,
)
from doc_manager.chunking.tokenizer import WhitespaceTokenizer
from doc_manager.extraction.normalize import NormalizedPage, normalize_text


def _page(index: int, page_number: int | None, words: int, word: str = "w") -> NormalizedPage:
    # normalize_text keeps single spaces, so token count == word count.
    return NormalizedPage(index=index, page_number=page_number, text=" ".join([word] * words))


def _profile(target: int, overlap: int) -> ChunkingProfile:
    return ChunkingProfile(
        target_tokens=target, overlap_tokens=overlap, tokenizer_id=WhitespaceTokenizer.id
    )


def test_small_pages_are_packed_within_target() -> None:
    pages = [_page(i, i + 1, 30) for i in range(5)]  # 5 pages * 30 tokens
    chunks = chunk_pages(pages, profile=_profile(target=100, overlap=10))
    # 30+30+30 = 90 <= 100; adding a 4th (120) overflows -> new chunk.
    assert [c.token_count for c in chunks] == [90, 60]
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 3)
    assert (chunks[1].page_start, chunks[1].page_end) == (4, 5)


def test_page_boundary_is_respected_no_cross_when_pages_fit() -> None:
    # Each page equals the target exactly, so every page is its own chunk.
    pages = [_page(i, i + 1, 50) for i in range(3)]
    chunks = chunk_pages(pages, profile=_profile(target=50, overlap=10))
    assert [(c.page_start, c.page_end) for c in chunks] == [(1, 1), (2, 2), (3, 3)]


def test_oversized_page_is_split_with_overlap_inside_one_page() -> None:
    pages = [_page(0, 7, 250)]  # one 250-token page, target 100, overlap 20 -> step 80
    chunks = chunk_pages(pages, profile=_profile(target=100, overlap=20))
    # windows start at 0, 80, 160: sizes 100, 100, 90 (last reaches the end and
    # stops — no duplicate tail window).
    assert [c.token_count for c in chunks] == [100, 100, 90]
    # Every sub-chunk stays within the single physical page.
    assert all(c.page_start == 7 and c.page_end == 7 for c in chunks)
    # Consecutive windows overlap by `overlap` tokens.
    first = chunks[0].text.split()
    second = chunks[1].text.split()
    assert first[-20:] == second[:20]


def test_blank_pages_are_skipped() -> None:
    pages = [_page(0, 1, 10), NormalizedPage(1, 2, "   "), _page(2, 3, 10)]
    chunks = chunk_pages(pages, profile=_profile(target=100, overlap=10))
    assert len(chunks) == 1
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 3)


def test_indices_are_dense_and_zero_based() -> None:
    pages = [_page(0, 1, 200)]
    chunks = chunk_pages(pages, profile=_profile(target=50, overlap=10))
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_pageless_text_format_yields_none_ranges() -> None:
    pages = [NormalizedPage(0, None, "alpha beta gamma"), NormalizedPage(1, None, "delta")]
    chunks = chunk_pages(pages, profile=_profile(target=100, overlap=10))
    assert len(chunks) == 1
    assert chunks[0].page_start is None and chunks[0].page_end is None


def test_chunking_is_deterministic() -> None:
    pages = [_page(i, i + 1, 40, word=f"w{i}") for i in range(6)]
    profile = default_chunking_profile()
    a = chunk_pages(pages, profile=profile)
    b = chunk_pages(pages, profile=profile)
    assert [(c.index, c.text_hash, c.page_start, c.page_end) for c in a] == [
        (c.index, c.text_hash, c.page_start, c.page_end) for c in b
    ]


def test_text_hash_matches_normalized_content() -> None:
    import hashlib

    pages = [_page(0, 1, 5)]
    chunk = chunk_pages(pages, profile=_profile(target=100, overlap=10))[0]
    expected = hashlib.sha256(normalize_text(chunk.text).encode("utf-8")).hexdigest()
    assert chunk.text_hash == expected


def test_empty_input_returns_no_chunks() -> None:
    assert chunk_pages([], profile=default_chunking_profile()) == []


def test_chunk_id_is_stable_and_profile_sensitive() -> None:
    content = uuid.uuid4()
    p1 = _profile(target=750, overlap=100).hash
    p2 = _profile(target=500, overlap=100).hash
    assert chunk_id(content, p1, 0) == chunk_id(content, p1, 0)
    assert chunk_id(content, p1, 0) != chunk_id(content, p1, 1)
    assert chunk_id(content, p1, 0) != chunk_id(content, p2, 0)
    assert chunk_id(content, p1, 0) != chunk_id(uuid.uuid4(), p1, 0)


def test_profile_hash_changes_with_each_knob() -> None:
    base = {
        "version": CHUNKING_VERSION,
        "target_tokens": 750,
        "overlap_tokens": 100,
        "tokenizer_id": "whitespace-1",
    }
    h = chunking_profile_hash(**base)
    assert h != chunking_profile_hash(**{**base, "target_tokens": 751})
    assert h != chunking_profile_hash(**{**base, "overlap_tokens": 101})
    assert h != chunking_profile_hash(**{**base, "tokenizer_id": "wordpiece-1"})
    assert h != chunking_profile_hash(**{**base, "version": "chunk-2"})


def test_profile_rejects_invalid_budgets() -> None:
    import pytest

    with pytest.raises(ValueError):
        ChunkingProfile(target_tokens=0, overlap_tokens=0, tokenizer_id="whitespace-1")
    with pytest.raises(ValueError):
        ChunkingProfile(target_tokens=100, overlap_tokens=100, tokenizer_id="whitespace-1")
    with pytest.raises(ValueError):
        ChunkingProfile(target_tokens=100, overlap_tokens=-1, tokenizer_id="whitespace-1")


def test_chunk_is_frozen() -> None:
    import dataclasses

    import pytest

    c = Chunk(index=0, text="x", token_count=1, page_start=1, page_end=1, text_hash="h")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.index = 1  # type: ignore[misc]
