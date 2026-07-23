"""phase 4.d chunks

Adds the `chunks` table: the SQL authority for retrieval chunks derived from a
content object under a chunking profile. Rows are metadata only (chunk text lives
in the Qdrant point payload and the extracted artifact), so a consistency check
can diff SQL chunk rows against vector points. The primary key is the
deterministic chunk id, making re-index an idempotent upsert.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-22 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_object_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.BigInteger(), nullable=False),
        sa.Column("page_start", sa.BigInteger(), nullable=True),
        sa.Column("page_end", sa.BigInteger(), nullable=True),
        sa.Column("token_count", sa.BigInteger(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("chunking_profile_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_profile_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["content_object_id"],
            ["content_objects.id"],
            name=op.f("fk_chunks_content_object_id_content_objects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint(
            "content_object_id",
            "chunking_profile_hash",
            "chunk_index",
            name="uq_chunks_identity",
        ),
    )
    op.create_index("ix_chunks_content_object", "chunks", ["content_object_id"])


def downgrade() -> None:
    op.drop_index("ix_chunks_content_object", table_name="chunks")
    op.drop_table("chunks")
