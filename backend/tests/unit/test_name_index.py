"""File-name index: embedding text for a path, and name-first result ordering."""

from __future__ import annotations

import uuid

from doc_manager.retrieval.service import _name_first
from doc_manager.vectors import SearchHit, file_name_text


def test_name_text_drops_extension_separators_and_sync_conflict_suffix() -> None:
    text = file_name_text(
        "2615 Documents/Appliences Receipts and Warranties/fridge from 2018  - 4 years "
        "warranty_DiskStation_Dec-10-1230-2022_DownloadConflict.pdf"
    )
    assert text == (
        "fridge from 2018 4 years warranty (2615 Documents / Appliences Receipts and "
        "Warranties / fridge from 2018 4 years warranty)"
    )


def test_name_text_top_level_file_is_just_the_name() -> None:
    assert file_name_text("NYResidential_Lease_Agreement2026.docx") == (
        "NYResidential Lease Agreement2026"
    )
    assert file_name_text("Chase 401K.pdf") == "Chase 401K"


def test_name_text_handles_windows_separators() -> None:
    assert file_name_text("Taxes\\2024\\w2_albert.pdf") == "w2 albert (Taxes / 2024 / w2 albert)"


def _hit(chunk: str, content: str) -> SearchHit:
    return SearchHit(
        id=uuid.uuid4(),
        score=0.5,
        content_object_id=content,
        chunk_id=chunk,
        chunk_index=0,
        page_start=None,
        page_end=None,
        text=chunk,
    )


def test_name_first_orders_by_name_match_caps_per_file_and_dedupes() -> None:
    led = [_hit("b1", "B"), _hit("a1", "A"), _hit("a2", "A"), _hit("a3", "A")]
    regular = [_hit("x1", "X"), _hit("a1", "A"), _hit("y1", "Y")]
    out = _name_first(led, regular, name_docs=["A", "B"], per_file=2, top_k=5)
    # A (best name match) first with at most 2 passages, then B, then the rest
    # of the regular ranking without the already-led a1.
    assert [h.chunk_id for h in out] == ["a1", "a2", "b1", "x1", "y1"]


def test_name_first_respects_top_k() -> None:
    led = [_hit("a1", "A"), _hit("a2", "A")]
    regular = [_hit("x1", "X")]
    assert [h.chunk_id for h in _name_first(led, regular, ["A"], 2, top_k=2)] == ["a1", "a2"]
