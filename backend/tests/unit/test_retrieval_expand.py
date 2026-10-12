"""Keyword-leg query expansion (hybrid retrieval, part B).

``expand_keyword_query`` only shapes the OR keyword leg; the vector leg still
embeds the raw question, so these assertions are about *widening* term coverage.
"""

from __future__ import annotations

from doc_manager.retrieval.service import expand_keyword_query


def _terms(query: str) -> set[str]:
    return set(expand_keyword_query(query).split())


def test_strips_possessive_and_lowercases() -> None:
    terms = _terms("Albert's last job")
    assert "albert" in terms  # "Albert's" -> "albert", not "albert's"
    assert "alberts" not in terms


def test_adds_synonyms_for_known_terms() -> None:
    terms = _terms("Albert's last job")
    # "job" fans out to document phrasings so the resume chunk can match.
    assert {"role", "position", "employment", "experience"} <= terms


def test_keeps_original_terms_and_order_first() -> None:
    expanded = expand_keyword_query("job work").split()
    assert expanded[:2] == ["job", "work"]  # originals lead, synonyms follow
    assert "employment" in expanded


def test_drops_sub_two_char_and_symbol_tokens() -> None:
    assert expand_keyword_query("a ? !") == ""


def test_no_duplicate_terms() -> None:
    # "work" and "job" both expand to "employment"/"experience"; no repeats.
    expanded = expand_keyword_query("job work experience").split()
    assert len(expanded) == len(set(expanded))
