"""Persisted read-only sync plans (TECHSTACK sections 5.14, 6; Phase 7.c).

A ``sync_plans`` row is an immutable dry-run comparison of a source location
against a target; ``sync_plan_items`` are its classified per-entry results
(``already_present`` / ``copy`` / ``conflict`` / ``manual_review``). There are **no
execution columns** and no code path that mutates any source: the MVP compares and
plans only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from doc_manager.db.models.base import Base
from doc_manager.domain.enums import SyncPlanStatus


class SyncPlan(Base):
    __tablename__ = "sync_plans"
    __table_args__ = (Index("ix_sync_plans_created", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_locations.id", ondelete="CASCADE")
    )
    target_location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_locations.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(20), default=SyncPlanStatus.building.value)
    item_count: Mapped[int] = mapped_column(BigInteger, default=0)
    covered_percent: Mapped[float] = mapped_column(Float, default=0.0)
    #: Per-action coverage counts (already_present/copy/conflict/manual_review).
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    built_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())


class SyncPlanItem(Base):
    __tablename__ = "sync_plan_items"
    __table_args__ = (Index("ix_sync_plan_items_plan", "plan_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sync_plans.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(40))
    source_relative_path: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_text_hash: Mapped[str] = mapped_column(String(64))
    #: Matched target (a copy has none).
    target_relative_path: Mapped[str | None] = mapped_column(Text, default=None)
    target_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
