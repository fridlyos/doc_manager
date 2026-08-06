"""Pure location comparison + pairwise coverage (TECHSTACK 5.14, §14 Phase 7.a/7.b).

Compares a **source** location against a **target** by relative path, file hash
(sha256), and normalized text hash, classifying each source entry into one of the
four contract actions and summarizing pairwise coverage. Pure and deterministic:
no DB, no filesystem — the ``build_sync_plan`` handler (7.c) feeds it catalog rows
and persists the result. There is no execution path anywhere in this module.

Directional: the source is authoritative; the report says what the target is
missing, what conflicts, and what already matches. A bidirectional view is two
comparisons.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum


class SyncAction(StrEnum):
    #: Same relative path and same file hash — an exact match.
    already_present = "already_present"
    #: No equivalent in the target — a proposed copy source→target.
    copy = "copy"
    #: Same relative path, different file hash — same name, different bytes.
    conflict = "conflict"
    #: Content equivalent exists under a different path (renamed) or as
    #: text-equivalent (different bytes/pagination) — a human decides.
    manual_review = "manual_review"


# Stable, safe reason codes (no paths beyond the item's own fields).
REASON_EXACT = "exact_match"
REASON_PATH_HASH_MISMATCH = "path_hash_mismatch"
REASON_RENAMED = "renamed"
REASON_TEXT_EQUIVALENT = "text_equivalent"
REASON_MISSING = "missing_in_target"


@dataclass(frozen=True, slots=True)
class EntryRow:
    """One indexed catalog entry's comparison keys."""

    relative_path: str
    display_path: str
    sha256: str
    text_hash: str


@dataclass(frozen=True, slots=True)
class LocationSnapshot:
    entries: list[EntryRow] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SyncItem:
    action: SyncAction
    reason: str
    source_relative_path: str
    source_sha256: str
    source_text_hash: str
    target_relative_path: str | None = None
    target_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    total_source: int
    already_present: int
    copy: int
    conflict: int
    manual_review: int

    @property
    def covered(self) -> int:
        """Source entries with a content equivalent in the target."""
        return self.already_present + self.manual_review

    @property
    def covered_percent(self) -> float:
        if self.total_source == 0:
            return 0.0
        return round(self.covered / self.total_source * 100, 1)


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    items: list[SyncItem]
    coverage: CoverageSummary


def compare_locations(source: LocationSnapshot, target: LocationSnapshot) -> ComparisonResult:
    """Classify each source entry against the target and summarize coverage.

    Precedence: a path match (exact or conflict) wins over a hash match elsewhere,
    which wins over a text-equivalent, which wins over missing.
    """
    by_path = {e.relative_path: e for e in target.entries}
    by_sha: dict[str, list[EntryRow]] = defaultdict(list)
    by_text: dict[str, list[EntryRow]] = defaultdict(list)
    for e in target.entries:
        by_sha[e.sha256].append(e)
        by_text[e.text_hash].append(e)

    items = [_classify(s, by_path, by_sha, by_text) for s in source.entries]
    counts = dict.fromkeys(SyncAction, 0)
    for item in items:
        counts[item.action] += 1
    coverage = CoverageSummary(
        total_source=len(source.entries),
        already_present=counts[SyncAction.already_present],
        copy=counts[SyncAction.copy],
        conflict=counts[SyncAction.conflict],
        manual_review=counts[SyncAction.manual_review],
    )
    return ComparisonResult(items=items, coverage=coverage)


def _classify(
    source: EntryRow,
    by_path: dict[str, EntryRow],
    by_sha: dict[str, list[EntryRow]],
    by_text: dict[str, list[EntryRow]],
) -> SyncItem:
    same_path = by_path.get(source.relative_path)
    if same_path is not None:
        if same_path.sha256 == source.sha256:
            return _item(SyncAction.already_present, REASON_EXACT, source, same_path)
        return _item(SyncAction.conflict, REASON_PATH_HASH_MISMATCH, source, same_path)

    renamed = _first(by_sha.get(source.sha256))
    if renamed is not None:
        return _item(SyncAction.manual_review, REASON_RENAMED, source, renamed)

    text_equal = _first(by_text.get(source.text_hash))
    if text_equal is not None:
        return _item(SyncAction.manual_review, REASON_TEXT_EQUIVALENT, source, text_equal)

    return _item(SyncAction.copy, REASON_MISSING, source, None)


def _first(candidates: list[EntryRow] | None) -> EntryRow | None:
    """Deterministic pick among equivalent targets: lowest relative path."""
    if not candidates:
        return None
    return min(candidates, key=lambda e: e.relative_path)


def _item(action: SyncAction, reason: str, source: EntryRow, target: EntryRow | None) -> SyncItem:
    return SyncItem(
        action=action,
        reason=reason,
        source_relative_path=source.relative_path,
        source_sha256=source.sha256,
        source_text_hash=source.text_hash,
        target_relative_path=target.relative_path if target else None,
        target_sha256=target.sha256 if target else None,
    )
