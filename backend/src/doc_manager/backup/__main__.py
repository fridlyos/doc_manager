"""Backup maintenance CLI (Phase 8.c).

    python -m doc_manager.backup run       # produce a completed backup set
    python -m doc_manager.backup verify ID # verify a completed set's checksums

Run through the maintenance compose profile, which mounts the local staging volume
and the NAS backup path only into this one-shot service.
"""

from __future__ import annotations

import sys
from pathlib import Path

from doc_manager.backup.checksums import verify_sums
from doc_manager.backup.default_steps import DefaultSteps
from doc_manager.backup.runner import COMPLETED_MARKER, run_backup
from doc_manager.core.config import get_settings
from doc_manager.core.logging import configure_logging, get_logger

log = get_logger("doc_manager.backup.cli")


def _config_snapshot(settings: object) -> dict[str, object]:
    """Non-secret configuration recorded in the manifest (never secrets)."""
    s = settings
    return {
        "embedding_model": getattr(s, "embedding_model", None),
        "qdrant_collection": getattr(s, "qdrant_collection", None),
        "chunk_target_tokens": getattr(s, "chunk_target_tokens", None),
        "chunk_overlap_tokens": getattr(s, "chunk_overlap_tokens", None),
    }


def main(argv: list[str]) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=True)
    command = argv[0] if argv else "run"

    staging = Path(argv_env("DOCMAN_STAGING_ROOT", "/staging"))
    backups = Path(argv_env("DOCMAN_BACKUP_ROOT", "/backups"))

    if command == "run":
        result = run_backup(
            staging_root=staging,
            backup_root=backups,
            steps=DefaultSteps(settings),
            config_snapshot=_config_snapshot(settings),
            retention_daily=settings.backup_retention_daily,
            retention_weekly=settings.backup_retention_weekly,
            retention_monthly=settings.backup_retention_monthly,
        )
        log.info("backup_run_ok", backup_id=result.backup_id, pruned=len(result.pruned))
        return 0

    if command == "verify":
        if len(argv) < 2:
            log.error("verify_usage", detail="verify <backup-id>")
            return 2
        set_dir = backups / "completed" / argv[1]
        if not (set_dir / COMPLETED_MARKER).is_file():
            log.error("verify_incomplete", detail="missing completion marker")
            return 1
        verified = verify_sums(set_dir)
        log.info(
            "verify_result",
            ok=verified.ok,
            checked=verified.checked,
            mismatches=verified.mismatches,
            missing=verified.missing,
        )
        return 0 if verified.ok else 1

    log.error("unknown_command", command=command)
    return 2


def argv_env(name: str, default: str) -> str:
    import os

    return os.environ.get(name, default)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
