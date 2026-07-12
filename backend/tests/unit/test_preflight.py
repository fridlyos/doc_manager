from __future__ import annotations

from pathlib import Path

from doc_manager.core.preflight import (
    check_backup_probe,
    check_live_storage_local,
    check_source_sentinel,
    filesystem_type,
)


def test_sentinel_match(tmp_path: Path) -> None:
    (tmp_path / ".docman-source-id").write_text("loc-123", encoding="utf-8")
    result = check_source_sentinel(tmp_path, "loc-123", ".docman-source-id")
    assert result.ok


def test_sentinel_mismatch(tmp_path: Path) -> None:
    (tmp_path / ".docman-source-id").write_text("other", encoding="utf-8")
    result = check_source_sentinel(tmp_path, "loc-123", ".docman-source-id")
    assert not result.ok


def test_sentinel_missing(tmp_path: Path) -> None:
    result = check_source_sentinel(tmp_path, "loc-123", ".docman-source-id")
    assert not result.ok
    assert "missing" in result.detail


def test_backup_probe_roundtrip(tmp_path: Path) -> None:
    result = check_backup_probe(tmp_path)
    assert result.ok
    # Probe file must be cleaned up.
    assert list(tmp_path.iterdir()) == []


def test_backup_probe_missing_dir(tmp_path: Path) -> None:
    result = check_backup_probe(tmp_path / "nope")
    assert not result.ok


def test_filesystem_type_longest_mount_wins(tmp_path: Path) -> None:
    mounts = tmp_path / "mounts"
    mounts.write_text(
        "rootfs / rootfs rw 0 0\n//nas/docs /sources/nas cifs rw 0 0\ntmpfs /dev tmpfs rw 0 0\n",
        encoding="utf-8",
    )
    assert filesystem_type(Path("/sources/nas/legal"), mounts_source=mounts) == "cifs"
    assert filesystem_type(Path("/var"), mounts_source=mounts) == "rootfs"


def test_live_storage_rejects_network_fs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("doc_manager.core.preflight.filesystem_type", lambda *a, **k: "cifs")
    result = check_live_storage_local(tmp_path, role="postgres")
    assert not result.ok
    assert "cifs" in result.detail


def test_live_storage_accepts_local_fs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("doc_manager.core.preflight.filesystem_type", lambda *a, **k: "ext4")
    result = check_live_storage_local(tmp_path, role="qdrant")
    assert result.ok
