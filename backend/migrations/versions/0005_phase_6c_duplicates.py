"""phase 6.c duplicate report tables

Materialized, rebuildable duplicate report: ``duplicate_groups`` (exact/text) and
``duplicate_members`` (denormalized display path/location/state per active entry).
Derived from authoritative hashes by ``build_duplicate_report``.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-26 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "duplicate_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("group_hash", sa.String(length=64), nullable=False),
        sa.Column("member_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_duplicate_groups")),
    )
    op.create_index("ix_duplicate_groups_kind", "duplicate_groups", ["kind"])

    op.create_table(
        "duplicate_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_entry_id", sa.Uuid(), nullable=False),
        sa.Column("source_location_id", sa.Uuid(), nullable=False),
        sa.Column("display_path", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["duplicate_groups.id"],
            name=op.f("fk_duplicate_members_group_id_duplicate_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["catalog_entry_id"],
            ["catalog_entries.id"],
            name=op.f("fk_duplicate_members_catalog_entry_id_catalog_entries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_duplicate_members")),
    )
    op.create_index("ix_duplicate_members_group", "duplicate_members", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_duplicate_members_group", table_name="duplicate_members")
    op.drop_table("duplicate_members")
    op.drop_index("ix_duplicate_groups_kind", table_name="duplicate_groups")
    op.drop_table("duplicate_groups")
