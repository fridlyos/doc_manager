"""Real restore steps (TECHSTACK 11.4). Used by the maintenance CLI.

Shells out to ``psql`` (globals) and ``pg_restore`` (the custom-format dump) into
an **empty** target database. Needs the postgresql-client tools (the maintenance
image ships ``postgresql-client-16``, matching the server major) and a reachable
PostgreSQL. Not exercised by unit tests (those inject fakes). Mirrors
``backup.default_steps``; nothing here touches a source document root.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from doc_manager.backup.default_steps import libpq_url
from doc_manager.core.config import Settings
from doc_manager.core.logging import get_logger

log = get_logger("doc_manager.restore.steps")

# On-disk filenames the backup coordinator writes (see backup.default_steps).
_POSTGRES_DUMP = "postgres.dump"
_POSTGRES_GLOBALS = "globals.sql"


class DefaultRestoreSteps:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def restore_globals(self, set_dir: Path) -> None:
        """Restore cluster globals (roles). Tolerant: a pre-existing role is not a
        failure, so ``ON_ERROR_STOP`` stays off for this step only."""
        subprocess.run(
            [
                "psql",
                "--dbname",
                libpq_url(self._settings.database_url),
                "-v",
                "ON_ERROR_STOP=0",
                "-f",
                str(set_dir / _POSTGRES_GLOBALS),
            ],
            check=True,
        )

    def restore_postgres(self, set_dir: Path) -> None:
        """Restore the custom-format dump into the (empty) target database. The
        ``--clean --if-exists`` drops are harmless NOTICEs on an empty database."""
        subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--dbname",
                libpq_url(self._settings.database_url),
                str(set_dir / _POSTGRES_DUMP),
            ],
            check=True,
        )
