"""phase 7.c sync plans

Persisted read-only dry-run sync plans: ``sync_plans`` (compared source/target,
status, coverage summary) and ``sync_plan_items`` (classified per-entry results).
No execution columns — the MVP compares and plans only.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-06 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_location_id", sa.Uuid(), nullable=False),
        sa.Column("target_location_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("item_count", sa.BigInteger(), nullable=False),
        sa.Column("covered_percent", sa.Float(), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_location_id"],
            ["source_locations.id"],
            name=op.f("fk_sync_plans_source_location_id_source_locations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_location_id"],
            ["source_locations.id"],
            name=op.f("fk_sync_plans_target_location_id_source_locations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_plans")),
    )
    op.create_index("ix_sync_plans_created", "sync_plans", ["created_at"])

    op.create_table(
        "sync_plan_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("source_relative_path", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("target_relative_path", sa.Text(), nullable=True),
        sa.Column("target_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["sync_plans.id"],
            name=op.f("fk_sync_plan_items_plan_id_sync_plans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_plan_items")),
    )
    op.create_index("ix_sync_plan_items_plan", "sync_plan_items", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_sync_plan_items_plan", table_name="sync_plan_items")
    op.drop_table("sync_plan_items")
    op.drop_index("ix_sync_plans_created", table_name="sync_plans")
    op.drop_table("sync_plans")
