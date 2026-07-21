"""Content identity and per-version indexing state (TECHSTACK section 6).

`content_objects` is reusable structured extracted content, keyed by its
structure hash plus the extraction/normalization profile — text-equivalent files
with different pagination stay separate for citation correctness. `file_versions`
records the observed bytes of a catalog entry at a point in time and links to the
content object once extraction succeeds (or carries an extraction error).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from doc_manager.db.models.base import Base
from doc_manager.domain.enums import ExtractionStatus


class ContentObject(Base):
    __tablename__ = "content_objects"
    __table_args__ = (
        # Reuse key: identical structured content under the same profile/version
        # is stored once (TECHSTACK 5.4).
        UniqueConstraint(
            "structure_hash",
            "extraction_profile_hash",
            "normalization_version",
            name="uq_content_objects_identity",
        ),
        Index("ix_content_objects_text_hash", "text_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    #: Pagination-insensitive text hash (text-duplicate detection).
    text_hash: Mapped[str] = mapped_column(String(64))
    #: Pagination-sensitive structure hash (reuse authority).
    structure_hash: Mapped[str] = mapped_column(String(64))
    extractor_name: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(64))
    extraction_profile_hash: Mapped[str] = mapped_column(String(64))
    normalization_version: Mapped[str] = mapped_column(String(32))
    #: Path relative to the artifact root; the compressed extracted text.
    artifact_path: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(BigInteger)
    character_count: Mapped[int] = mapped_column(BigInteger)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())


class FileVersion(Base):
    __tablename__ = "file_versions"
    __table_args__ = (Index("ix_file_versions_catalog_entry", "catalog_entry_id"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    catalog_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_entries.id", ondelete="CASCADE")
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[datetime] = mapped_column()
    sha256: Mapped[str] = mapped_column(String(64))
    content_object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_objects.id", ondelete="SET NULL"), default=None
    )
    extraction_status: Mapped[str] = mapped_column(
        String(20), default=ExtractionStatus.pending.value
    )
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    observed_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    indexed_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.clock_timestamp(), onupdate=func.clock_timestamp()
    )
