"""Compressed, content-addressed storage for extracted text (TECHSTACK 5.6)."""

from doc_manager.artifact_store.extracted_text import (
    ArtifactStore,
    StoredArtifact,
    content_address,
)

__all__ = ["ArtifactStore", "StoredArtifact", "content_address"]
