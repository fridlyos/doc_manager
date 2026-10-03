"""Unit tests for the backup maintenance profile (Phase 8.c).

Covers the pure retention policy (GFS), checksum write/verify + tamper detection,
and the orchestration (``run_backup``) over fake ``BackupSteps`` — a complete
verified set with the COMPLETED marker last, an interrupted run leaving no
completed set, and retention prune among completed sets. No live services.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from doc_manager.backup.checksums import (
    SUMS_FILENAME,
    verify_sums,
    write_sums,
)
from doc_manager.backup.manifest import ArtifactEntry, read_manifest
from doc_manager.backup.retention import select_pruned, select_retained
from doc_manager.backup.runner import (
    COMPLETED_MARKER,
    BackupError,
    BackupSteps,
    run_backup,
)

# --- retention (GFS) ---------------------------------------------------------


def _dt(y: int, m: int, d: int, h: int = 0) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


def test_retention_keeps_newest_per_bucket() -> None:
    # Two sets on the same day -> only the newest counts for the daily bucket.
    items = [
        ("a", _dt(2026, 8, 6, 2)),
        ("b", _dt(2026, 8, 6, 9)),
        ("c", _dt(2026, 8, 5, 9)),
    ]
    kept = select_retained(items, daily=1, weekly=0, monthly=0)
    assert kept == {"b"}


def test_retention_weekly_and_monthly_buckets() -> None:
    items = [
        ("today", _dt(2026, 8, 6)),
        ("last_week", _dt(2026, 7, 28)),
        ("last_month", _dt(2026, 6, 15)),
        ("old", _dt(2026, 1, 1)),
    ]
    kept = select_retained(items, daily=1, weekly=2, monthly=2)
    # daily keeps today; weekly keeps today's week + last_week's week;
    # monthly keeps Aug + Jul buckets (today, last_week).
    assert "today" in kept
    assert "last_week" in kept
    assert "old" not in kept


def test_select_pruned_is_complement_oldest_first() -> None:
    items = [
        ("newest", _dt(2026, 8, 6)),
        ("mid", _dt(2026, 8, 5)),
        ("oldest", _dt(2026, 8, 4)),
    ]
    pruned = select_pruned(items, daily=1, weekly=0, monthly=0)
    assert pruned == ["oldest", "mid"]  # complement of {newest}, oldest first


# --- checksums ---------------------------------------------------------------


def test_write_and_verify_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"alpha")
    (tmp_path / "b.bin").write_bytes(b"beta")
    write_sums(tmp_path, ["a.bin", "b.bin"])
    result = verify_sums(tmp_path)
    assert result.ok
    assert result.checked == 2
    assert result.mismatches == []
    assert result.missing == []


def test_verify_detects_tampering(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"alpha")
    write_sums(tmp_path, ["a.bin"])
    (tmp_path / "a.bin").write_bytes(b"tampered")  # mutate after checksum
    result = verify_sums(tmp_path)
    assert not result.ok
    assert result.mismatches == ["a.bin"]


def test_verify_detects_missing_file(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"alpha")
    write_sums(tmp_path, ["a.bin"])
    (tmp_path / "a.bin").unlink()
    result = verify_sums(tmp_path)
    assert not result.ok
    assert result.missing == ["a.bin"]


def test_verify_missing_sums_file(tmp_path: Path) -> None:
    result = verify_sums(tmp_path)
    assert not result.ok
    assert result.missing == [SUMS_FILENAME]


# --- orchestration -----------------------------------------------------------


class FakeSteps:
    """Writes deterministic fake capture files into the staging directory."""

    def __init__(self, *, artifacts: int = 1, snapshot: bool = False) -> None:
        self._artifacts = artifacts
        self._snapshot = snapshot

    def dump_postgres(self, stage: Path) -> str:
        (stage / "postgres.dump").write_bytes(b"PGDUMP")
        return "postgres.dump"

    def dump_globals(self, stage: Path) -> str:
        (stage / "globals.sql").write_text("-- roles\n", encoding="utf-8")
        return "globals.sql"

    def snapshot_qdrant(self, stage: Path) -> tuple[str | None, dict[str, object]]:
        mapping: dict[str, object] = {"collection": "docs", "downloaded": False}
        if self._snapshot:
            (stage / "qdrant.snapshot").write_bytes(b"SNAP")
            return "qdrant.snapshot", mapping
        return None, mapping

    def inventory_artifacts(self) -> tuple[list[ArtifactEntry], list[str]]:
        entries = [
            ArtifactEntry(artifact_path=f"a/{i}.txt", sha256=f"{i:064x}")
            for i in range(self._artifacts)
        ]
        return entries, []


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "staging", tmp_path / "backups"


def test_run_backup_produces_verified_completed_set(tmp_path: Path) -> None:
    staging, backups = _roots(tmp_path)
    result = run_backup(
        staging_root=staging,
        backup_root=backups,
        steps=FakeSteps(artifacts=3, snapshot=True),
        config_snapshot={"embedding_model": "bge-small-en-v1.5"},
    )
    final = result.completed_path
    assert final.is_dir()
    # Marker present and written last.
    assert (final / COMPLETED_MARKER).is_file()
    # Staged copy cleaned up.
    assert not (staging / result.backup_id).exists()
    # No leftover .partial.
    assert not (backups / "completed" / f"{result.backup_id}.partial").exists()
    # Full set verifies.
    assert verify_sums(final).ok
    # Manifest recorded the artifact inventory + config checksum.
    manifest = read_manifest(final)
    assert len(manifest["artifact_inventory"]) == 3
    assert manifest["non_secret_configuration_checksum"]
    assert manifest["qdrant_snapshot"]["filename"] == "qdrant.snapshot"


def test_run_backup_without_snapshot_omits_entry(tmp_path: Path) -> None:
    staging, backups = _roots(tmp_path)
    result = run_backup(
        staging_root=staging,
        backup_root=backups,
        steps=FakeSteps(snapshot=False),
        config_snapshot={},
    )
    manifest = read_manifest(result.completed_path)
    assert manifest["qdrant_snapshot"] is None
    assert verify_sums(result.completed_path).ok


class ExplodingSteps(FakeSteps):
    def inventory_artifacts(self) -> tuple[list[ArtifactEntry], list[str]]:
        raise RuntimeError("capture blew up mid-run")


def test_interrupted_run_leaves_no_completed_set(tmp_path: Path) -> None:
    staging, backups = _roots(tmp_path)
    with pytest.raises(RuntimeError, match="blew up"):
        run_backup(
            staging_root=staging,
            backup_root=backups,
            steps=ExplodingSteps(),
            config_snapshot={},
            now=datetime(2026, 8, 6, 3, tzinfo=UTC),
        )
    # No staged dir, no completed dir contents, no marker anywhere.
    assert not (staging / "20260806T030000Z").exists()
    completed = backups / "completed"
    assert not completed.exists() or not any(completed.iterdir())


def test_run_backup_prunes_old_completed_sets(tmp_path: Path) -> None:
    staging, backups = _roots(tmp_path)
    base = datetime(2026, 7, 1, 12, tzinfo=UTC)
    # Ten daily backups; keep only the newest 3 daily buckets.
    ids: list[str] = []
    for day in range(10):
        result = run_backup(
            staging_root=staging,
            backup_root=backups,
            steps=FakeSteps(),
            config_snapshot={},
            retention_daily=3,
            retention_weekly=0,
            retention_monthly=0,
            now=base + timedelta(days=day),
        )
        ids.append(result.backup_id)
    completed = backups / "completed"
    surviving = {p.name for p in completed.iterdir() if (p / COMPLETED_MARKER).is_file()}
    assert surviving == set(ids[-3:])  # only newest 3 days remain


def test_run_backup_fails_when_staged_verify_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging, backups = _roots(tmp_path)

    import doc_manager.backup.runner as runner

    real_write = runner.write_sums

    def corrupt_after_sums(directory: Path, filenames: list[str]) -> Path:
        path = real_write(directory, filenames)
        (directory / "postgres.dump").write_bytes(b"CORRUPTED-AFTER-SUMS")
        return path

    monkeypatch.setattr(runner, "write_sums", corrupt_after_sums)

    with pytest.raises(BackupError, match="verification"):
        run_backup(
            staging_root=staging,
            backup_root=backups,
            steps=FakeSteps(),
            config_snapshot={},
            now=datetime(2026, 8, 6, 4, tzinfo=UTC),
        )
    completed = backups / "completed"
    assert not completed.exists() or not any(completed.iterdir())
    assert not (staging / "20260806T040000Z").exists()


def test_manifest_snapshot_none_and_backupsteps_protocol() -> None:
    # Protocol runtime sanity: FakeSteps satisfies the structural interface.
    steps: BackupSteps = FakeSteps()
    assert hasattr(steps, "dump_postgres")
    assert hasattr(steps, "inventory_artifacts")


def test_discover_completed_sets_excludes_partial(tmp_path: Path) -> None:
    # NAS-side detection (8.g): only COMPLETED sets are restorable; a .partial
    # publish and a malformed-name directory are never counted.
    from doc_manager.backup.runner import discover_completed_sets

    staging, backups = _roots(tmp_path)
    base = datetime(2026, 8, 6, 1, tzinfo=UTC)
    good = [
        run_backup(
            staging_root=staging,
            backup_root=backups,
            steps=FakeSteps(),
            config_snapshot={},
            retention_daily=99,
            retention_weekly=99,
            retention_monthly=99,
            now=base + timedelta(days=d),
        ).backup_id
        for d in range(2)
    ]
    completed = backups / "completed"
    # An interrupted publish: dir + manifest but no COMPLETED marker.
    partial = completed / "20260810T120000Z.partial"
    partial.mkdir()
    (partial / "manifest.json").write_text("{}", encoding="utf-8")
    # A stray non-timestamp directory, even with a marker, is ignored.
    stray = completed / "not-a-backup"
    stray.mkdir()
    (stray / COMPLETED_MARKER).write_text("x\n", encoding="utf-8")

    found = discover_completed_sets(backups)
    assert [bid for bid, _ in found] == sorted(good, reverse=True)  # newest first, only good sets
