"""Materialized duplicate report (TECHSTACK section 5.4, Phase 6.c).

Duplicate groups are *derived* from authoritative hashes (file SHA-256 and
normalized text hash), so these tables are a rebuildable cache for UI performance
— ``build_duplicate_report`` truncates and repopulates them. Each member carries a
denormalized display path / location / state so a group lists every active
location and path without a re-join at read time.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from doc_manager.db.models.base import Base


class DuplicateGroup(Base):
    __tablename__ = "duplicate_groups"
    __table_args__ = (Index("ix_duplicate_groups_kind", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    #: ``exact`` (shared file SHA-256) or ``text`` (shared normalized text across
    #: distinct file hashes / pagination).
    kind: Mapped[str] = mapped_column(String(10))
    #: The sha256 (exact) or text_hash (text) the group is keyed on.
    group_hash: Mapped[str] = mapped_column(String(64))
    member_count: Mapped[int] = mapped_column(BigInteger)
    built_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())


class DuplicateMember(Base):
    __tablename__ = "duplicate_members"
    __table_args__ = (Index("ix_duplicate_members_group", "group_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("duplicate_groups.id", ondelete="CASCADE")
    )
    catalog_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_entries.id", ondelete="CASCADE")
    )
    source_location_id: Mapped[uuid.UUID] = mapped_column()
    #: Server-resolved display path (never a scan root).
    display_path: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20))
    sha256: Mapped[str] = mapped_column(String(64))
