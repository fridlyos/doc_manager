"""Versioned normalization: hash sensitivity and determinism."""

from __future__ import annotations

from doc_manager.extraction.base import ExtractedDocument, ExtractedPage
from doc_manager.extraction.normalize import (
    NORMALIZATION_VERSION,
    normalize,
    normalize_text,
)


def _doc(pages: list[tuple[int | None, str]]) -> ExtractedDocument:
    return ExtractedDocument(
        extractor_name="x",
        extractor_version="x-1",
        pages=[ExtractedPage(index=i, page_number=pn, text=t) for i, (pn, t) in enumerate(pages)],
    )


def test_normalize_text_collapses_whitespace_and_nfc() -> None:
    assert normalize_text("  a\t\n  b  ") == "a b"
    # Composed vs decomposed forms of "é" normalize identically.
    assert normalize_text("é") == normalize_text("é")


def test_normalization_is_deterministic() -> None:
    a = normalize(_doc([(1, "Hello world"), (2, "Second page")]))
    b = normalize(_doc([(1, "Hello world"), (2, "Second page")]))
    assert a.text_hash == b.text_hash
    assert a.structure_hash == b.structure_hash
    assert a.normalization_version == NORMALIZATION_VERSION


def test_text_hash_is_pagination_insensitive() -> None:
    one_page = normalize(_doc([(1, "the quick brown fox jumps")]))
    two_pages = normalize(_doc([(1, "the quick brown"), (2, "fox jumps")]))
    # Same words, different page split -> identical text hash...
    assert one_page.text_hash == two_pages.text_hash
    # ...but the structure hash captures the different pagination.
    assert one_page.structure_hash != two_pages.structure_hash


def test_structure_hash_changes_with_content() -> None:
    a = normalize(_doc([(1, "alpha")]))
    b = normalize(_doc([(1, "beta")]))
    assert a.structure_hash != b.structure_hash
    assert a.text_hash != b.text_hash
