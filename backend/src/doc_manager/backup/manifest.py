"""Backup-set manifest (TECHSTACK 11.5, Phase 8.c).

A machine-readable description of one application-aware backup set: the PostgreSQL
dump + globals, the optional Qdrant snapshot and its collection/alias/profile
mapping (snapshots do not carry aliases), the extracted-text artifact inventory
with checksums, and a non-secret configuration checksum. **No secrets** ever enter
a manifest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_FILENAME = "manifest.json"
MANIFEST_VERSION = "1"


@dataclass(frozen=True, slots=True)
class FileEntry:
    filename: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ArtifactEntry:
    #: Path relative to the artifact root (never a host scan root).
    artifact_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Manifest:
    backup_id: str
    created_at: str
    postgres_dump: FileEntry
    postgres_globals: FileEntry
    qdrant_snapshot: FileEntry | None
    qdrant_mapping: dict[str, Any]
    artifact_inventory: list[ArtifactEntry]
    non_secret_configuration_checksum: str
    manifest_version: str = MANIFEST_VERSION
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    def write(self, directory: Path) -> Path:
        path = directory / MANIFEST_FILENAME
        path.write_text(self.to_json(), encoding="utf-8")
        return path


def read_manifest(directory: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((directory / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    return data


def config_checksum(config_snapshot: dict[str, Any]) -> str:
    """Deterministic checksum of a **non-secret** configuration snapshot."""
    canonical = json.dumps(config_snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
