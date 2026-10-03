"""Backup orchestration (TECHSTACK 11.5, Phase 8.c).

Coordinates one application-aware backup set with the mandated discipline: build in
local **staging**, checksum + manifest, **verify**, then atomically publish to
``completed/<id>`` and write the ``COMPLETED`` marker **last** — so an interrupted
run never leaves a set that looks restorable. Retention prune runs after a
successful publish.

The external steps (``pg_dump``, ``pg_dumpall --globals-only``, the Qdrant snapshot,
and the artifact inventory) are injected as a ``BackupSteps`` implementation, so the
orchestration is fully testable without a live PostgreSQL/Qdrant. ``default_steps``
provides the real implementations. Nothing here writes to a source root.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from doc_manager.backup.checksums import sha256_file, verify_sums, write_sums
from doc_manager.backup.manifest import (
    MANIFEST_FILENAME,
    ArtifactEntry,
    FileEntry,
    Manifest,
    config_checksum,
)
from doc_manager.backup.retention import select_pruned
from doc_manager.core.logging import get_logger

log = get_logger("doc_manager.backup")

COMPLETED_MARKER = "COMPLETED"


class BackupError(Exception):
    """A backup failed; no completed set was produced."""


@dataclass(frozen=True, slots=True)
class StepOutputs:
    postgres_dump: str
    postgres_globals: str
    qdrant_snapshot: str | None
    qdrant_mapping: dict[str, Any]
    artifacts: list[ArtifactEntry]
    warnings: list[str]


class BackupSteps(Protocol):
    """The external captures. Implementations write files into ``stage``."""

    def dump_postgres(self, stage: Path) -> str: ...
    def dump_globals(self, stage: Path) -> str: ...
    def snapshot_qdrant(self, stage: Path) -> tuple[str | None, dict[str, Any]]: ...
    def inventory_artifacts(self) -> tuple[list[ArtifactEntry], list[str]]: ...


@dataclass(frozen=True, slots=True)
class BackupResult:
    backup_id: str
    completed_path: Path
    pruned: list[str]


def run_backup(
    *,
    staging_root: Path,
    backup_root: Path,
    steps: BackupSteps,
    config_snapshot: dict[str, Any],
    retention_daily: int = 14,
    retention_weekly: int = 8,
    retention_monthly: int = 12,
    now: datetime | None = None,
) -> BackupResult:
    now = now or datetime.now(UTC)
    backup_id = now.strftime("%Y%m%dT%H%M%SZ")
    stage = staging_root / backup_id
    stage.mkdir(parents=True, exist_ok=False)

    try:
        out = _capture(steps, stage)
        manifest = _build_manifest(backup_id, now, stage, out, config_snapshot)
        manifest.write(stage)
        files = [out.postgres_dump, out.postgres_globals, MANIFEST_FILENAME]
        if out.qdrant_snapshot:
            files.append(out.qdrant_snapshot)
        write_sums(stage, files)
        result = verify_sums(stage)
        if not result.ok:
            raise BackupError(
                f"staged set failed verification: mismatches={result.mismatches} "
                f"missing={result.missing}"
            )
        completed = _publish(stage, backup_root, backup_id)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    else:
        shutil.rmtree(stage, ignore_errors=True)  # staging is scratch; don't accumulate

    pruned = _prune(backup_root, retention_daily, retention_weekly, retention_monthly)
    log.info(
        "backup_completed",
        backup_id=backup_id,
        artifacts=len(out.artifacts),
        qdrant_snapshot=bool(out.qdrant_snapshot),
        pruned=len(pruned),
    )
    return BackupResult(backup_id=backup_id, completed_path=completed, pruned=pruned)


def _capture(steps: BackupSteps, stage: Path) -> StepOutputs:
    pg = steps.dump_postgres(stage)
    globals_file = steps.dump_globals(stage)
    snapshot, mapping = steps.snapshot_qdrant(stage)
    artifacts, warnings = steps.inventory_artifacts()
    return StepOutputs(
        postgres_dump=pg,
        postgres_globals=globals_file,
        qdrant_snapshot=snapshot,
        qdrant_mapping=mapping,
        artifacts=artifacts,
        warnings=warnings,
    )


def _build_manifest(
    backup_id: str,
    now: datetime,
    stage: Path,
    out: StepOutputs,
    config_snapshot: dict[str, Any],
) -> Manifest:
    snapshot_entry = (
        FileEntry(out.qdrant_snapshot, sha256_file(stage / out.qdrant_snapshot))
        if out.qdrant_snapshot
        else None
    )
    return Manifest(
        backup_id=backup_id,
        created_at=now.isoformat(),
        postgres_dump=FileEntry(out.postgres_dump, sha256_file(stage / out.postgres_dump)),
        postgres_globals=FileEntry(out.postgres_globals, sha256_file(stage / out.postgres_globals)),
        qdrant_snapshot=snapshot_entry,
        qdrant_mapping=out.qdrant_mapping,
        artifact_inventory=out.artifacts,
        non_secret_configuration_checksum=config_checksum(config_snapshot),
        warnings=out.warnings,
    )


def _publish(stage: Path, backup_root: Path, backup_id: str) -> Path:
    """Copy staged set to ``completed/<id>.partial``, verify, atomic-rename, mark."""
    completed_dir = backup_root / "completed"
    completed_dir.mkdir(parents=True, exist_ok=True)
    partial = completed_dir / f"{backup_id}.partial"
    final = completed_dir / backup_id
    if partial.exists():
        shutil.rmtree(partial)
    shutil.copytree(stage, partial)
    if not verify_sums(partial).ok:
        shutil.rmtree(partial, ignore_errors=True)
        raise BackupError("published copy failed verification")
    partial.rename(final)  # atomic on the same filesystem
    (final / COMPLETED_MARKER).write_text(f"{backup_id}\n", encoding="utf-8")  # marker LAST
    return final


def discover_completed_sets(backup_root: Path) -> list[tuple[str, datetime]]:
    """Restorable sets under ``completed/``, newest first.

    A set counts only when it carries the ``COMPLETED`` marker and its directory
    name is a valid backup-id timestamp. An interrupted ``<id>.partial`` publish is
    excluded — the same rule the NAS side uses to decide a set is restorable, so a
    half-copied set is never counted or restored.
    """
    completed_dir = backup_root / "completed"
    if not completed_dir.is_dir():
        return []
    dated: list[tuple[str, datetime]] = []
    for entry in completed_dir.iterdir():
        if not (entry / COMPLETED_MARKER).is_file():
            continue  # only fully-completed sets (skips <id>.partial)
        try:
            when = datetime.strptime(entry.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        dated.append((entry.name, when))
    return sorted(dated, key=lambda pair: pair[1], reverse=True)


def _prune(backup_root: Path, daily: int, weekly: int, monthly: int) -> list[str]:
    completed_dir = backup_root / "completed"
    dated = discover_completed_sets(backup_root)
    pruned = select_pruned(dated, daily=daily, weekly=weekly, monthly=monthly)
    for backup_id in pruned:
        shutil.rmtree(completed_dir / backup_id, ignore_errors=True)
    return pruned
