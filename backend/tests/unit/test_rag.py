"""RAG core (Phase 5.e): evidence selection, grounded prompt, citation mapping.

Pure/offline. Verifies per-content caps, token budgeting, alias assignment, the
grounded-prompt shape, and server-owned citation mapping (ordinals, dropped
invented aliases, untrusted-evidence framing).
"""

from __future__ import annotations

from doc_manager.generation.rag import (
    INSUFFICIENT_MARKER,
    SYSTEM_INSTRUCTIONS,
    UNKNOWN_CITATION_WARNING,
    EvidenceSet,
    build_grounded_prompt,
    is_insufficient,
    map_citations,
    select_evidence,
)
from doc_manager.retrieval.service import ResolvedPath, SearchResult


def _result(
    *,
    chunk: str,
    content: str,
    text: str,
    score: float = 0.8,
    page: int | None = 1,
    source: str = "loc-1",
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk,
        content_object_id=content,
        score=score,
        page_start=page,
        page_end=page,
        text=text,
        snippet=text[:40],
        availability="current",
        paths=[
            ResolvedPath(
                catalog_entry_id=f"entry-{chunk}",
                source_location_id=source,
                display_path=f"/docs/{chunk}.txt",
                state="indexed",
                is_primary=True,
            )
        ],
    )


def test_select_assigns_sequential_aliases() -> None:
    results = [
        _result(chunk="a", content="c1", text="alpha one two"),
        _result(chunk="b", content="c2", text="bravo three four"),
    ]
    ev = select_evidence(results, token_budget=100, max_per_content=3, max_blocks=10)
    assert [b.alias for b in ev.blocks] == ["E1", "E2"]
    assert ev.total_tokens == 6


def test_select_caps_per_content_object() -> None:
    results = [
        _result(chunk="a", content="c1", text="one"),
        _result(chunk="b", content="c1", text="two"),
        _result(chunk="c", content="c1", text="three"),
        _result(chunk="d", content="c2", text="four"),
    ]
    ev = select_evidence(results, token_budget=100, max_per_content=2, max_blocks=10)
    # Only 2 of c1's chunks, plus c2's.
    contents = [b.content_object_id for b in ev.blocks]
    assert contents.count("c1") == 2
    assert "c2" in contents


def test_select_respects_token_budget_and_skips_oversized() -> None:
    results = [
        _result(chunk="a", content="c1", text="w " * 10),  # 10 tokens
        _result(chunk="b", content="c2", text="w " * 50),  # 50 tokens, over remaining
        _result(chunk="c", content="c3", text="w " * 3),  # 3 tokens, fits
    ]
    ev = select_evidence(results, token_budget=15, max_per_content=3, max_blocks=10)
    # 10 fits; 50 skipped; 3 fits -> total 13.
    assert [b.chunk_id for b in ev.blocks] == ["a", "c"]
    assert ev.total_tokens == 13


def test_select_truncates_first_when_oversized() -> None:
    results = [_result(chunk="a", content="c1", text="w " * 100)]
    ev = select_evidence(results, token_budget=20, max_per_content=3, max_blocks=10)
    assert len(ev.blocks) == 1
    assert ev.total_tokens == 20


def test_select_max_blocks() -> None:
    results = [_result(chunk=str(i), content=f"c{i}", text="tok") for i in range(20)]
    ev = select_evidence(results, token_budget=1000, max_per_content=3, max_blocks=5)
    assert len(ev.blocks) == 5


def test_empty_results_is_empty_set() -> None:
    ev = select_evidence([], token_budget=100, max_per_content=3, max_blocks=10)
    assert ev.is_empty


def test_build_grounded_prompt_shape() -> None:
    ev = select_evidence(
        [
            _result(chunk="a", content="c1", text="renews in december", page=4),
            _result(chunk="b", content="c2", text="unrelated", page=None),
        ],
        token_budget=100,
        max_per_content=3,
        max_blocks=10,
    )
    req = build_grounded_prompt(question="when does it renew?", evidence=ev, max_output_tokens=200)
    assert req.user_prompt == "when does it renew?"
    assert req.max_output_tokens == 200
    assert SYSTEM_INSTRUCTIONS.split("\n", 1)[0] in req.system_prompt
    assert "untrusted document text" in req.system_prompt
    assert "[E1] (page 4) renews in december" in req.system_prompt
    assert "[E2] unrelated" in req.system_prompt  # pageless -> no page tag


def _evidence() -> EvidenceSet:
    return select_evidence(
        [
            _result(chunk="a", content="c1", text="alpha", page=2),
            _result(chunk="b", content="c2", text="bravo", page=5),
        ],
        token_budget=100,
        max_per_content=3,
        max_blocks=10,
    )


def test_map_citations_rewrites_to_ordinals_by_first_appearance() -> None:
    ev = _evidence()
    # Model cites E2 before E1 -> ordinals follow appearance order.
    mapping = map_citations("It is bravo [E2] and also alpha [E1].", ev)
    assert mapping.answer == "It is bravo [1] and also alpha [2]."
    assert [(c.citation_id, c.ordinal, c.chunk_id) for c in mapping.citations] == [
        ("E2", 1, "b"),
        ("E1", 2, "a"),
    ]
    assert mapping.citations[0].page_start == 5
    assert mapping.warnings == []


def test_map_citations_drops_invented_alias_with_warning() -> None:
    ev = _evidence()
    mapping = map_citations("Alpha [E1] and phantom [E9].", ev)
    assert mapping.answer == "Alpha [1] and phantom ."
    assert [c.citation_id for c in mapping.citations] == ["E1"]
    assert UNKNOWN_CITATION_WARNING in mapping.warnings


def test_map_citations_repeated_alias_keeps_one_citation() -> None:
    ev = _evidence()
    mapping = map_citations("alpha [E1] again [E1]", ev)
    assert mapping.answer == "alpha [1] again [1]"
    assert len(mapping.citations) == 1


def test_map_citations_no_markers_yields_no_citations() -> None:
    ev = _evidence()
    mapping = map_citations("A plain answer with no citations.", ev)
    assert mapping.citations == []
    assert mapping.answer == "A plain answer with no citations."


def test_evidence_source_policies_defaults_deny_for_unknown() -> None:
    ev = select_evidence(
        [
            _result(chunk="a", content="c1", text="x", source="allow-src"),
            _result(chunk="b", content="c2", text="y", source="deny-src"),
        ],
        token_budget=100,
        max_per_content=3,
        max_blocks=10,
    )
    policies = ev.evidence_source_policies({"allow-src": "allow"})
    # deny-src is unknown to the map -> defaults to deny (fail closed).
    assert sorted(policies) == ["allow", "deny"]


def test_is_insufficient() -> None:
    assert is_insufficient(f"  {INSUFFICIENT_MARKER}  ")
    assert not is_insufficient("The office opens at 9am [E1].")
