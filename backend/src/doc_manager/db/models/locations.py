"""Source location configuration (TECHSTACK section 6, `source_locations`).

`scan_root` is the worker-visible container path; `display_root` is what the UI
shows the user (for example a UNC path). `sentinel_id` is the expected content
of the mount sentinel file: NULL means "not yet adopted" (advisory sentinel,
e.g. the in-repo synthetic corpus); a first successful scan adopts the observed
sentinel value, and every later scan must match it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from doc_manager.db.models.base import Base
from doc_manager.domain.enums import ExternalGenerationPolicy, PathStyle


class SourceLocation(Base):
    __tablename__ = "source_locations"
    # UPDATE must RETURNING the server-side onupdate columns: "auto" only
    # covers INSERT, leaving updated_at expired (sync lazy load -> MissingGreenlet).
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    scan_root: Mapped[str] = mapped_column(Text)
    display_root: Mapped[str] = mapped_column(Text)
    path_style: Mapped[str] = mapped_column(String(20), default=PathStyle.linux.value)
    enabled: Mapped[bool] = mapped_column(default=True)
    read_only: Mapped[bool] = mapped_column(default=True)
    external_generation_policy: Mapped[str] = mapped_column(
        String(10), default=ExternalGenerationPolicy.deny.value
    )
    # Lowercase extensions without dots; empty list means "all supported".
    include_extensions: Mapped[list[str]] = mapped_column(default=list)
    exclude_globs: Mapped[list[str]] = mapped_column(default=list)
    # NULL disables scheduled scans; manual scans remain available.
    scan_interval_minutes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    sentinel_id: Mapped[str | None] = mapped_column(Text, default=None)
    last_successful_scan_at: Mapped[datetime | None] = mapped_column(default=None)
    # Optimistic-concurrency revision surfaced as the API ETag (contract sec. 7).
    revision: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.clock_timestamp(), onupdate=func.clock_timestamp()
    )
