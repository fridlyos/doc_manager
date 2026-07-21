"""Compressed, versioned, content-addressed extracted-text artifacts.

Artifacts hold the normalized page/section records so re-chunking never reopens
the source file. The path is derived from the structure hash, extraction profile,
and normalization version, so identical structured content is written once and
reused (TECHSTACK section 5.6). Writes go to a temp file and are atomically
renamed, so a reader never sees a partial artifact, and an interrupted write
leaves no corrupt file at the final path. Original documents are never copied
here — only extracted text.
"""

from __future__ import annotations

import gzip
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doc_manager.extraction.normalize import NormalizedDocument, NormalizedPage

_ARTIFACT_FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    #: Path relative to the artifact root; stored on the content object.
    relative_path: str
    #: True when an identical artifact already existed and no bytes were written.
    reused: bool


def content_address(
    structure_hash: str, extraction_profile_hash: str, normalization_version: str
) -> str:
    """Relative artifact path for a structured-content identity.

    Sharded by the first two hex chars to keep directories small. The profile
    and normalization version are in the name so the same structure extracted
    under a different profile/version is a distinct artifact.
    """
    stem = f"{structure_hash}.{extraction_profile_hash[:12]}"
    return f"{normalization_version}/{structure_hash[:2]}/{stem}.json.gz"


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def store(
        self,
        normalized: NormalizedDocument,
        *,
        extractor_name: str,
        extractor_version: str,
        extraction_profile_hash: str,
        metadata: dict[str, Any] | None = None,
    ) -> StoredArtifact:
        relative = content_address(
            normalized.structure_hash,
            extraction_profile_hash,
            normalized.normalization_version,
        )
        dest = self._root / relative
        if dest.exists():
            return StoredArtifact(relative_path=relative, reused=True)

        payload = {
            "artifact_format_version": _ARTIFACT_FORMAT_VERSION,
            "extractor_name": extractor_name,
            "extractor_version": extractor_version,
            "extraction_profile_hash": extraction_profile_hash,
            "normalization_version": normalized.normalization_version,
            "text_hash": normalized.text_hash,
            "structure_hash": normalized.structure_hash,
            "metadata": metadata or {},
            "pages": [
                {"index": page.index, "page_number": page.page_number, "text": page.text}
                for page in normalized.pages
            ],
        }
        blob = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / f".{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_bytes(blob)
            os.replace(tmp, dest)  # atomic within the same directory
        finally:
            tmp.unlink(missing_ok=True)
        return StoredArtifact(relative_path=relative, reused=False)

    def load(self, relative_path: str) -> dict[str, Any]:
        blob = (self._root / relative_path).read_bytes()
        loaded: dict[str, Any] = json.loads(gzip.decompress(blob).decode("utf-8"))
        return loaded

    def load_pages(self, relative_path: str) -> list[NormalizedPage]:
        return [
            NormalizedPage(index=p["index"], page_number=p["page_number"], text=p["text"])
            for p in self.load(relative_path)["pages"]
        ]
