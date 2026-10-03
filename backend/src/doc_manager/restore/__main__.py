"""Restore maintenance CLI (Phase 8 DoD).

    python -m doc_manager.restore run ID            # restore a completed set into an empty DB
    python -m doc_manager.restore verify-consistency # SQL<->vector drift gate (exit 1 if not clean)

Run through the maintenance compose profile. ``run`` restores PostgreSQL only;
rebuild the vector index afterwards with
``POST /api/v1/system/reindex?rebuild_vectors=true`` (the worker re-reads the live
sources), then gate with ``verify-consistency``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from doc_manager.core.config import Settings, get_settings
from doc_manager.core.logging import configure_logging, get_logger
from doc_manager.db.session import create_engine, create_session_factory
from doc_manager.embedding import resolve_embedding_profile
from doc_manager.jobs.handlers.consistency import ConsistencyReport, scan_consistency
from doc_manager.restore.default_steps import DefaultRestoreSteps
from doc_manager.restore.runner import RestoreError, run_restore
from doc_manager.vectors import build_qdrant_repository

log = get_logger("doc_manager.restore.cli")


def argv_env(name: str, default: str) -> str:
    return os.environ.get(name, default)


async def _read_consistency(settings: Settings) -> ConsistencyReport:
    """Build a session + repo and scan SQL<->vector drift in-process.

    Seam: unit tests monkeypatch this so the CLI exit-code mapping is covered
    without a live PostgreSQL/Qdrant. The embedding profile is resolved from the
    registry (no model download)."""
    engine = create_engine(settings)
    try:
        profile = resolve_embedding_profile(settings)
        repo = build_qdrant_repository(settings, profile)
        async with create_session_factory(engine)() as session:
            return await scan_consistency(session, repo, profile.hash)
    finally:
        await engine.dispose()


def main(argv: list[str]) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=True)
    command = argv[0] if argv else ""
    backups = Path(argv_env("DOCMAN_BACKUP_ROOT", "/backups"))

    if command == "run":
        if len(argv) < 2:
            log.error("run_usage", detail="run <backup-id>")
            return 2
        try:
            result = run_restore(
                backup_root=backups,
                backup_id=argv[1],
                steps=DefaultRestoreSteps(settings),
            )
        except RestoreError as exc:
            log.error("restore_failed", detail=str(exc))
            return 1
        log.info("restore_run_ok", backup_id=result.backup_id)
        return 0

    if command == "verify-consistency":
        report = asyncio.run(_read_consistency(settings))
        log.info(
            "verify_consistency_result",
            clean=report.clean,
            content_objects_checked=report.content_objects_checked,
            chunks_expected=report.chunks_expected,
            points_found=report.points_found,
            missing_points=report.missing_points,
            orphan_points=report.orphan_points,
        )
        return 0 if report.clean else 1

    log.error("unknown_command", command=command)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
