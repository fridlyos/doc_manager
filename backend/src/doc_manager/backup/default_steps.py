"""Real backup capture steps (TECHSTACK 11.5). Used by the maintenance CLI.

Shells out to ``pg_dump``/``pg_dumpall``, asks Qdrant for a collection snapshot,
and inventories the extracted-text artifacts referenced by ``content_objects``.
Not exercised by unit tests (those inject fakes) — it needs a live PostgreSQL,
Qdrant, and the artifact root. Nothing here writes to a source document root.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select

from doc_manager.backup.checksums import sha256_file
from doc_manager.backup.manifest import ArtifactEntry
from doc_manager.core.config import Settings
from doc_manager.core.logging import get_logger
from doc_manager.db.models import ContentObject

log = get_logger("doc_manager.backup.steps")

_POSTGRES_DUMP = "postgres.dump"
_POSTGRES_GLOBALS = "globals.sql"
_QDRANT_SNAPSHOT = "qdrant-snapshot.snapshot"


def _libpq_url(database_url: str) -> str:
    """Strip the SQLAlchemy driver so ``pg_dump`` gets a plain libpq URI."""
    return database_url.replace("postgresql+psycopg://", "postgresql://")


class DefaultSteps:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def dump_postgres(self, stage: Path) -> str:
        subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                f"--file={stage / _POSTGRES_DUMP}",
                _libpq_url(self._settings.database_url),
            ],
            check=True,
        )
        return _POSTGRES_DUMP

    def dump_globals(self, stage: Path) -> str:
        with (stage / _POSTGRES_GLOBALS).open("w", encoding="utf-8") as fh:
            subprocess.run(
                [
                    "pg_dumpall",
                    "--globals-only",
                    "--dbname",
                    _libpq_url(self._settings.database_url),
                ],
                check=True,
                stdout=fh,
            )
        return _POSTGRES_GLOBALS

    def snapshot_qdrant(self, stage: Path) -> tuple[str | None, dict[str, Any]]:
        """Record the active collection/profile mapping; snapshot best-effort.

        The Qdrant snapshot is a fast-recovery convenience — the vector index is
        rebuildable from the catalog (§11.4) — so a download failure is a warning,
        not a backup failure.
        """
        from doc_manager.embedding import resolve_embedding_profile

        profile = resolve_embedding_profile(self._settings)
        collection = profile.collection_name(self._settings.qdrant_collection)
        mapping: dict[str, Any] = {
            "collection": collection,
            "embedding_profile_hash": profile.hash,
            "embedding_model": profile.model_name,
        }
        try:
            from qdrant_client import QdrantClient

            client = QdrantClient(url=self._settings.qdrant_url)
            if client.collection_exists(collection):
                desc = client.create_snapshot(collection_name=collection)
                mapping["snapshot_name"] = getattr(desc, "name", None)
                # Download support varies by client/deployment; record the name and
                # leave retrieval to the operator/runbook when unavailable.
                return None, {**mapping, "downloaded": False}
            mapping["missing_collection"] = True
        except Exception as exc:  # noqa: BLE001 - snapshot is optional
            log.warning("qdrant_snapshot_skipped", detail=type(exc).__name__)
            mapping["error"] = type(exc).__name__
        return None, mapping

    def inventory_artifacts(self) -> tuple[list[ArtifactEntry], list[str]]:
        engine = create_engine(self._settings.database_url)
        artifact_root = Path(self._settings.artifact_root)
        entries: list[ArtifactEntry] = []
        warnings: list[str] = []
        try:
            with engine.connect() as conn:
                paths = conn.execute(select(ContentObject.artifact_path).distinct()).scalars().all()
        finally:
            engine.dispose()
        for rel in paths:
            target = artifact_root / rel
            if target.is_file():
                entries.append(ArtifactEntry(artifact_path=rel, sha256=sha256_file(target)))
            else:
                warnings.append(f"missing_artifact:{rel}")
        return entries, warnings
