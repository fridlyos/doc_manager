"""Content-aware reconciliation lifecycle: add/change/move/restore/missing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from doc_manager.domain.enums import CatalogEntryState
from doc_manager.jobs.handlers.reconcile import (
    CatalogRow,
    ObservedFile,
    reconcile,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, tzinfo=UTC)


def _obs(path: str, sha: str, *, size: int = 10, mtime: datetime = _T0) -> ObservedFile:
    return ObservedFile(
        relative_path=path,
        file_name=path.rsplit("/", 1)[-1],
        extension="txt",
        size_bytes=size,
        mtime=mtime,
        sha256=sha,
    )


def _row(
    path: str,
    sha: str | None,
    *,
    state: str = CatalogEntryState.indexed.value,
    size: int | None = 10,
    mtime: datetime | None = _T0,
) -> CatalogRow:
    return CatalogRow(
        id=uuid.uuid4(),
        relative_path=path,
        state=state,
        size_bytes=size,
        mtime=mtime,
        sha256=sha,
    )


def test_new_file_is_added() -> None:
    plan = reconcile([], [_obs("a.txt", "sha-a")])
    assert plan.counts() == {"added": 1}
    assert plan.adds[0].observed.relative_path == "a.txt"


def test_identical_file_is_unchanged() -> None:
    row = _row("a.txt", "sha-a")
    plan = reconcile([row], [_obs("a.txt", "sha-a")])
    assert plan.counts() == {"unchanged": 1}
    upd = plan.updates[0]
    assert upd.kind == "unchanged"
    assert upd.state == CatalogEntryState.indexed.value  # state preserved
    assert not plan.missing


def test_mtime_touch_same_bytes_is_metadata_only() -> None:
    row = _row("a.txt", "sha-a", mtime=_T0)
    plan = reconcile([row], [_obs("a.txt", "sha-a", mtime=_T1)])
    assert plan.counts() == {"metadata": 1}
    # No re-index: the indexed state is preserved.
    assert plan.updates[0].state == CatalogEntryState.indexed.value


def test_changed_bytes_requeues_for_index() -> None:
    row = _row("a.txt", "sha-old")
    plan = reconcile([row], [_obs("a.txt", "sha-new")])
    assert plan.counts() == {"changed": 1}
    assert plan.updates[0].state == CatalogEntryState.discovered.value


def test_unseen_file_becomes_missing() -> None:
    row = _row("a.txt", "sha-a")
    plan = reconcile([row], [])
    assert plan.counts() == {"missing": 1}
    assert plan.missing[0].entry_id == row.id


def test_rename_is_detected_as_move_not_add_plus_missing() -> None:
    row = _row("old/name.txt", "sha-a", state=CatalogEntryState.indexed.value)
    plan = reconcile([row], [_obs("new/name.txt", "sha-a")])
    assert plan.counts() == {"moved": 1}
    upd = plan.updates[0]
    assert upd.entry_id == row.id
    assert upd.observed.relative_path == "new/name.txt"
    # A move preserves the indexed state — no re-extraction/re-embedding.
    assert upd.state == CatalogEntryState.indexed.value
    assert not plan.adds and not plan.missing


def test_restored_missing_file_reconnects() -> None:
    row = _row("a.txt", "sha-a", state=CatalogEntryState.missing.value)
    plan = reconcile([row], [_obs("a.txt", "sha-a")])
    assert plan.counts() == {"restored": 1}
    upd = plan.updates[0]
    assert upd.clear_missing is True
    assert upd.state == CatalogEntryState.discovered.value


def test_move_from_missing_source() -> None:
    row = _row("gone.txt", "sha-a", state=CatalogEntryState.missing.value)
    plan = reconcile([row], [_obs("here.txt", "sha-a")])
    assert plan.counts() == {"moved": 1}
    upd = plan.updates[0]
    assert upd.entry_id == row.id
    assert upd.clear_missing is True
    assert upd.state == CatalogEntryState.discovered.value


def test_copy_keeps_original_and_adds_new() -> None:
    # Same content still present at its path; a second copy appears elsewhere.
    original = _row("a.txt", "sha-a")
    plan = reconcile([original], [_obs("a.txt", "sha-a"), _obs("copy.txt", "sha-a")])
    counts = plan.counts()
    assert counts == {"unchanged": 1, "added": 1}


def test_two_disappear_one_reappears_moves_one_marks_other_missing() -> None:
    a = _row("a.txt", "sha-dup")
    b = _row("b.txt", "sha-dup")
    plan = reconcile([a, b], [_obs("c.txt", "sha-dup")])
    counts = plan.counts()
    assert counts == {"moved": 1, "missing": 1}
    moved_id = plan.updates[0].entry_id
    missing_id = plan.missing[0].entry_id
    assert {moved_id, missing_id} == {a.id, b.id}
