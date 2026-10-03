"""Restore orchestration (TECHSTACK 11.4-11.5, Phase 8 DoD).

Restores an application-aware backup set into an **empty** PostgreSQL database.
PostgreSQL is the authority (11.4); the vector index is rebuilt separately via
``POST /api/v1/system/reindex?rebuild_vectors=true`` (an async worker fan-out this
one-shot cannot cleanly await), then validated with the consistency check +
a known-query search.

Pure orchestration with a fail-closed precheck. The external steps (``psql`` for
globals, ``pg_restore`` for the dump) are injected as a ``RestoreSteps`` so the
flow is testable without a live PostgreSQL; ``default_steps`` provides the real
implementations. Mirrors ``backup.runner``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from doc_manager.backup.checksums import verify_sums
from doc_manager.backup.manifest import read_manifest
from doc_manager.backup.runner import COMPLETED_MARKER
from doc_manager.core.logging import get_logger

log = get_logger("doc_manager.restore")


class RestoreError(Exception):
    """A restore precheck failed; the database was not touched."""


class RestoreSteps(Protocol):
    def restore_globals(self, set_dir: Path) -> None: ...

    def restore_postgres(self, set_dir: Path) -> None: ...


@dataclass(frozen=True, slots=True)
class RestoreResult:
    backup_id: str
    set_dir: Path
    manifest: dict[str, Any]
    restored_globals: bool
    restored_postgres: bool


def run_restore(*, backup_root: Path, backup_id: str, steps: RestoreSteps) -> RestoreResult:
    """Verify a completed set, then restore globals + the PostgreSQL dump.

    Fails closed (``RestoreError``, no steps run) unless the set carries a
    ``COMPLETED`` marker, every checksum verifies, and the manifest is readable.
    Globals are restored before the dump so owned objects find their roles.
    """
    set_dir = backup_root / "completed" / backup_id
    manifest = _precheck(set_dir)
    log.info("restore_start", backup_id=backup_id, set_dir=str(set_dir))
    steps.restore_globals(set_dir)
    steps.restore_postgres(set_dir)
    log.info("restore_ok", backup_id=backup_id)
    return RestoreResult(
        backup_id=backup_id,
        set_dir=set_dir,
        manifest=manifest,
        restored_globals=True,
        restored_postgres=True,
    )


def _precheck(set_dir: Path) -> dict[str, Any]:
    if not set_dir.is_dir():
        raise RestoreError(f"no completed set: {set_dir}")
    if not (set_dir / COMPLETED_MARKER).is_file():
        raise RestoreError("set has no completion marker (not restorable)")
    verified = verify_sums(set_dir)
    if not verified.ok:
        raise RestoreError(
            "checksum verification failed: "
            f"{len(verified.mismatches)} mismatched, {len(verified.missing)} missing"
        )
    try:
        return read_manifest(set_dir)
    except (OSError, ValueError) as exc:  # unreadable / malformed manifest.json
        raise RestoreError(f"manifest unreadable: {exc}") from exc
