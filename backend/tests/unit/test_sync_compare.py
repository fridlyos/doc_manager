"""Pure location comparison + coverage (Phase 7.a/7.b): classification rules."""

from __future__ import annotations

from doc_manager.sync import (
    EntryRow,
    LocationSnapshot,
    SyncAction,
    compare_locations,
)
from doc_manager.sync.compare import (
    REASON_EXACT,
    REASON_MISSING,
    REASON_PATH_HASH_MISMATCH,
    REASON_RENAMED,
    REASON_TEXT_EQUIVALENT,
)


def _row(path: str, sha: str, text: str = "t") -> EntryRow:
    return EntryRow(relative_path=path, display_path=f"/d/{path}", sha256=sha, text_hash=text)


def _snap(*rows: EntryRow) -> LocationSnapshot:
    return LocationSnapshot(entries=list(rows))


def _by_path(result: object) -> dict[str, object]:
    return {i.source_relative_path: i for i in result.items}  # type: ignore[attr-defined]


def test_exact_match_is_already_present() -> None:
    src = _snap(_row("a.txt", "sha-a"))
    tgt = _snap(_row("a.txt", "sha-a"))
    result = compare_locations(src, tgt)
    item = result.items[0]
    assert item.action is SyncAction.already_present
    assert item.reason == REASON_EXACT
    assert item.target_relative_path == "a.txt"


def test_same_path_different_hash_is_conflict() -> None:
    result = compare_locations(_snap(_row("a.txt", "sha-a")), _snap(_row("a.txt", "sha-b")))
    item = result.items[0]
    assert item.action is SyncAction.conflict
    assert item.reason == REASON_PATH_HASH_MISMATCH
    assert item.target_sha256 == "sha-b"


def test_same_hash_different_path_is_renamed_manual_review() -> None:
    result = compare_locations(_snap(_row("a.txt", "sha-a")), _snap(_row("moved/a.txt", "sha-a")))
    item = result.items[0]
    assert item.action is SyncAction.manual_review
    assert item.reason == REASON_RENAMED
    assert item.target_relative_path == "moved/a.txt"


def test_text_equivalent_different_bytes_is_manual_review() -> None:
    src = _snap(_row("a.txt", "sha-a", text="shared"))
    tgt = _snap(_row("b.txt", "sha-b", text="shared"))  # same text, diff bytes + path
    result = compare_locations(src, tgt)
    item = result.items[0]
    assert item.action is SyncAction.manual_review
    assert item.reason == REASON_TEXT_EQUIVALENT


def test_no_equivalent_is_copy() -> None:
    result = compare_locations(_snap(_row("a.txt", "sha-a", text="x")), _snap())
    item = result.items[0]
    assert item.action is SyncAction.copy
    assert item.reason == REASON_MISSING
    assert item.target_relative_path is None


def test_path_conflict_outranks_hash_match_elsewhere() -> None:
    # Same path but different bytes (conflict) AND the source bytes exist under
    # another target path — the path match wins.
    src = _snap(_row("a.txt", "sha-a"))
    tgt = _snap(_row("a.txt", "sha-b"), _row("copy/a.txt", "sha-a"))
    item = _by_path(compare_locations(src, tgt))["a.txt"]
    assert item.action is SyncAction.conflict  # type: ignore[attr-defined]


def test_renamed_pick_is_deterministic_lowest_path() -> None:
    src = _snap(_row("a.txt", "sha-a"))
    tgt = _snap(_row("z/a.txt", "sha-a"), _row("b/a.txt", "sha-a"))
    item = compare_locations(src, tgt).items[0]
    assert item.target_relative_path == "b/a.txt"  # lowest path


def test_coverage_summary_counts_and_percent() -> None:
    src = _snap(
        _row("keep.txt", "s1"),  # already_present
        _row("moved.txt", "s2"),  # renamed -> manual_review
        _row("clash.txt", "s3"),  # conflict
        _row("new.txt", "s4", text="only-here"),  # copy
    )
    tgt = _snap(
        _row("keep.txt", "s1"),
        _row("elsewhere.txt", "s2"),
        _row("clash.txt", "s3-different"),
    )
    cov = compare_locations(src, tgt).coverage
    assert cov.total_source == 4
    assert cov.already_present == 1
    assert cov.manual_review == 1
    assert cov.conflict == 1
    assert cov.copy == 1
    assert cov.covered == 2  # already_present + manual_review
    assert cov.covered_percent == 50.0


def test_identical_locations_fully_covered() -> None:
    rows = [_row("a.txt", "s1"), _row("b.txt", "s2")]
    cov = compare_locations(LocationSnapshot(rows), LocationSnapshot(list(rows))).coverage
    assert cov.covered_percent == 100.0
    assert cov.copy == 0


def test_empty_target_all_missing() -> None:
    cov = compare_locations(_snap(_row("a", "s1"), _row("b", "s2")), _snap()).coverage
    assert cov.copy == 2
    assert cov.covered_percent == 0.0


def test_empty_source_is_zero_percent_no_divide_error() -> None:
    cov = compare_locations(_snap(), _snap(_row("a", "s1"))).coverage
    assert cov.total_source == 0
    assert cov.covered_percent == 0.0
