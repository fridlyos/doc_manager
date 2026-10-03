"""Application-aware restore (TECHSTACK 11.4-11.5, Phase 8 DoD).

Restores a completed backup set into an empty PostgreSQL database with a
fail-closed precheck (completion marker + checksums + manifest). PostgreSQL is
authoritative; the vector index is rebuilt afterwards from the catalog + live
sources via ``POST /system/reindex?rebuild_vectors=true`` and validated with the
consistency check. External steps are injected (``RestoreSteps``) so the flow is
testable without a live PostgreSQL; ``default_steps`` provides the real ones.
"""

from doc_manager.restore.runner import (
    RestoreError,
    RestoreResult,
    RestoreSteps,
    run_restore,
)

__all__ = [
    "RestoreError",
    "RestoreResult",
    "RestoreSteps",
    "run_restore",
]
