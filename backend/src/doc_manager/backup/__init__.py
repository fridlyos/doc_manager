"""Application-aware backup maintenance (TECHSTACK 11.5, Phase 8.c).

Coordinates a PostgreSQL dump + globals, an optional Qdrant snapshot + mapping, and
an artifact inventory into a checksummed, manifested, atomically-completed backup
set with retention. Pure orchestration + retention; external captures are injected
(``BackupSteps``) so the flow is testable without live services. Run via the
maintenance compose profile (``python -m doc_manager.backup``). Never writes to a
source root.
"""

from doc_manager.backup.checksums import VerifyResult, verify_sums
from doc_manager.backup.manifest import Manifest, read_manifest
from doc_manager.backup.retention import select_pruned, select_retained
from doc_manager.backup.runner import (
    BackupError,
    BackupResult,
    BackupSteps,
    run_backup,
)

__all__ = [
    "BackupError",
    "BackupResult",
    "BackupSteps",
    "Manifest",
    "VerifyResult",
    "read_manifest",
    "run_backup",
    "select_pruned",
    "select_retained",
    "verify_sums",
]
