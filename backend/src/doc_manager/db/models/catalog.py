"""Catalog entries and scan staging (TECHSTACK section 6; state machine sec. 8).

Phase 2 records *observations* only: one `catalog_entries` row per relative
path with the latest observed size/mtime denormalized onto it. `file_versions`
(SHA-256, content identity) arrive with extraction in Phase 3.

`scan_observations` is attempt-scoped staging. Rows are keyed by
`(job_id, attempt_number, lease_token)` and are invisible to catalog readers;
only the fenced final reconciliation transaction of a *complete* scan may fold
them into `catalog_entries`. An interrupted or stale scan leaves staging rows
behind, which are garbage-collected later — they never mark files missing.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from doc_manager.db.models.base import Base
from doc_manager.domain.enums import CatalogEntryState


class CatalogEntry(Base):
    __tablename__ = "catalog_entries"
    # See SourceLocation: ORM UPDATE must RETURNING onupdate columns.
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("source_location_id", "relative_path"),
        Index("ix_catalog_entries_location_state", "source_location_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_locations.id", ondelete="CASCADE")
    )
    relative_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    # Lowercase, no leading dot (API contract sec. 5.3).
    extension: Mapped[str] = mapped_column(String(32))
    mime_type: Mapped[str | None] = mapped_column(String(255), default=None)
    state: Mapped[str] = mapped_column(String(20), default=CatalogEntryState.discovered.value)
    # Latest scan observation, denormalized. Phase 3.a adds sha256 as the content
    # authority; size/mtime remain the fast change signal. Full file_versions
    # (content_object linkage, extraction status) arrive with extraction (3.b).
    last_observed_size_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    last_observed_mtime: Mapped[datetime | None] = mapped_column(default=None)
    # SHA-256 of the current content; NULL for entries observed before hashing
    # landed (a first Phase 3 scan hashes and backfills them).
    sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    # The file_version reflecting the entry's current bytes; set once indexing
    # runs. FK added out-of-line in the migration (circular with file_versions).
    current_file_version_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    first_seen_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    missing_since: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.clock_timestamp(), onupdate=func.clock_timestamp()
    )


class ScanObservation(Base):
    __tablename__ = "scan_observations"
    __table_args__ = (
        Index("ix_scan_observations_job_attempt", "job_id", "attempt_number"),
        UniqueConstraint("job_id", "attempt_number", "relative_path"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="CASCADE"))
    attempt_number: Mapped[int] = mapped_column(BigInteger)
    lease_token: Mapped[uuid.UUID] = mapped_column()
    relative_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[datetime] = mapped_column()
    # Content hash of the observed file. Carried from the catalog when size+mtime
    # are unchanged (fast path), otherwise freshly computed during enumeration.
    sha256: Mapped[str] = mapped_column(String(64))
    staged_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
