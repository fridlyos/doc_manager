"""Phase 8 DoD — restore orchestration (offline, mirrors test_backup.py).

Verifies the fail-closed precheck (completion marker + checksums + manifest), the
globals-before-dump ordering, step-failure propagation, and the
``verify-consistency`` CLI exit-code mapping. No live PostgreSQL/Qdrant — a valid
completed set is built on disk with a fake backup step, and restore/consistency
steps are injected fakes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_manager.backup.manifest import ArtifactEntry
from doc_manager.backup.runner import COMPLETED_MARKER, run_backup
from doc_manager.jobs.handlers.consistency import ConsistencyReport
from doc_manager.restore import RestoreError, RestoreResult, RestoreSteps, run_restore
from doc_manager.restore import __main__ as cli


class _BackupFakeSteps:
    """Minimal fake backup captures, enough to build a real verified set on disk."""

    def dump_postgres(self, stage: Path) -> str:
        (stage / "postgres.dump").write_bytes(b"PGDUMP")
        return "postgres.dump"

    def dump_globals(self, stage: Path) -> str:
        (stage / "globals.sql").write_text("-- roles\n", encoding="utf-8")
        return "globals.sql"

    def snapshot_qdrant(self, stage: Path) -> tuple[str | None, dict[str, object]]:
        return None, {"collection": "docs", "downloaded": False}

    def inventory_artifacts(self) -> tuple[list[ArtifactEntry], list[str]]:
        return [ArtifactEntry(artifact_path="a/0.txt", sha256=f"{0:064x}")], []


class _FakeRestoreSteps:
    """Records call order; optionally explodes on a named step."""

    def __init__(self, explode_on: str | None = None) -> None:
        self.calls: list[str] = []
        self._explode_on = explode_on

    def restore_globals(self, set_dir: Path) -> None:
        self.calls.append("restore_globals")
        if self._explode_on == "restore_globals":
            raise RuntimeError("globals restore blew up")

    def restore_postgres(self, set_dir: Path) -> None:
        self.calls.append("restore_postgres")
        if self._explode_on == "restore_postgres":
            raise RuntimeError("pg_restore blew up")


def _make_set(tmp_path: Path) -> tuple[Path, str]:
    """Build a valid completed backup set; return (backup_root, backup_id)."""
    backup_root = tmp_path / "backups"
    result = run_backup(
        staging_root=tmp_path / "staging",
        backup_root=backup_root,
        steps=_BackupFakeSteps(),
        config_snapshot={"embedding_model": "fake"},
    )
    return backup_root, result.backup_id


# --- precheck fails closed (no step runs) ------------------------------------


def test_precheck_fails_without_completed_marker(tmp_path: Path) -> None:
    backup_root, backup_id = _make_set(tmp_path)
    (backup_root / "completed" / backup_id / COMPLETED_MARKER).unlink()
    steps = _FakeRestoreSteps()
    with pytest.raises(RestoreError, match="completion marker"):
        run_restore(backup_root=backup_root, backup_id=backup_id, steps=steps)
    assert steps.calls == []


def test_precheck_fails_on_checksum_mismatch(tmp_path: Path) -> None:
    backup_root, backup_id = _make_set(tmp_path)
    (backup_root / "completed" / backup_id / "postgres.dump").write_bytes(b"TAMPERED")
    steps = _FakeRestoreSteps()
    with pytest.raises(RestoreError, match="checksum verification failed"):
        run_restore(backup_root=backup_root, backup_id=backup_id, steps=steps)
    assert steps.calls == []


def test_precheck_fails_on_missing_manifest(tmp_path: Path) -> None:
    backup_root, backup_id = _make_set(tmp_path)
    (backup_root / "completed" / backup_id / "manifest.json").unlink()
    steps = _FakeRestoreSteps()
    with pytest.raises(RestoreError):
        run_restore(backup_root=backup_root, backup_id=backup_id, steps=steps)
    assert steps.calls == []


def test_precheck_fails_for_unknown_set(tmp_path: Path) -> None:
    steps = _FakeRestoreSteps()
    with pytest.raises(RestoreError, match="no completed set"):
        run_restore(backup_root=tmp_path / "backups", backup_id="nope", steps=steps)
    assert steps.calls == []


# --- happy path + ordering ---------------------------------------------------


def test_globals_restored_before_postgres(tmp_path: Path) -> None:
    backup_root, backup_id = _make_set(tmp_path)
    steps = _FakeRestoreSteps()
    result = run_restore(backup_root=backup_root, backup_id=backup_id, steps=steps)
    assert steps.calls == ["restore_globals", "restore_postgres"]
    assert isinstance(result, RestoreResult)
    assert result.restored_globals and result.restored_postgres
    assert result.backup_id == backup_id
    assert result.manifest["backup_id"] == backup_id


def test_failing_globals_step_propagates_and_skips_postgres(tmp_path: Path) -> None:
    backup_root, backup_id = _make_set(tmp_path)
    steps = _FakeRestoreSteps(explode_on="restore_globals")
    with pytest.raises(RuntimeError, match="globals restore blew up"):
        run_restore(backup_root=backup_root, backup_id=backup_id, steps=steps)
    assert steps.calls == ["restore_globals"]  # postgres never attempted


def test_failing_postgres_step_after_globals(tmp_path: Path) -> None:
    backup_root, backup_id = _make_set(tmp_path)
    steps = _FakeRestoreSteps(explode_on="restore_postgres")
    with pytest.raises(RuntimeError, match="pg_restore blew up"):
        run_restore(backup_root=backup_root, backup_id=backup_id, steps=steps)
    assert steps.calls == ["restore_globals", "restore_postgres"]


# --- verify-consistency CLI exit codes (seam monkeypatched, no DB) -----------


def test_verify_consistency_exit_zero_when_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _clean(_settings: object) -> ConsistencyReport:
        return ConsistencyReport(content_objects_checked=2, chunks_expected=6, points_found=6)

    monkeypatch.setattr(cli, "_read_consistency", _clean)
    assert cli.main(["verify-consistency"]) == 0


def test_verify_consistency_exit_one_when_drifted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _drift(_settings: object) -> ConsistencyReport:
        return ConsistencyReport(chunks_expected=6, points_found=4, missing_points=2)

    monkeypatch.setattr(cli, "_read_consistency", _drift)
    assert cli.main(["verify-consistency"]) == 1


# --- structural --------------------------------------------------------------


def test_fake_restore_steps_satisfies_protocol() -> None:
    steps: RestoreSteps = _FakeRestoreSteps()
    assert hasattr(steps, "restore_globals") and hasattr(steps, "restore_postgres")
