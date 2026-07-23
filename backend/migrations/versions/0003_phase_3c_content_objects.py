"""phase 3.c content objects and file versions

Adds reusable structured content (`content_objects`), per-version indexing state
(`file_versions`), and the `catalog_entries.current_file_version_id` pointer
(FK added out-of-line because catalog_entries and file_versions reference each
other).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-21 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "content_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("structure_hash", sa.String(length=64), nullable=False),
        sa.Column("extractor_name", sa.String(length=64), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("extraction_profile_hash", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=32), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("page_count", sa.BigInteger(), nullable=False),
        sa.Column("character_count", sa.BigInteger(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_objects")),
        sa.UniqueConstraint(
            "structure_hash",
            "extraction_profile_hash",
            "normalization_version",
            name="uq_content_objects_identity",
        ),
    )
    op.create_index("ix_content_objects_text_hash", "content_objects", ["text_hash"])

    op.create_table(
        "file_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("catalog_entry_id", sa.Uuid(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_object_id", sa.Uuid(), nullable=True),
        sa.Column("extraction_status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["catalog_entry_id"],
            ["catalog_entries.id"],
            name=op.f("fk_file_versions_catalog_entry_id_catalog_entries"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["content_object_id"],
            ["content_objects.id"],
            name=op.f("fk_file_versions_content_object_id_content_objects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_versions")),
    )
    op.create_index("ix_file_versions_catalog_entry", "file_versions", ["catalog_entry_id"])

    op.add_column(
        "catalog_entries",
        sa.Column("current_file_version_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_catalog_entries_current_file_version_id_file_versions"),
        "catalog_entries",
        "file_versions",
        ["current_file_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_catalog_entries_current_file_version_id_file_versions"),
        "catalog_entries",
        type_="foreignkey",
    )
    op.drop_column("catalog_entries", "current_file_version_id")
    op.drop_index("ix_file_versions_catalog_entry", table_name="file_versions")
    op.drop_table("file_versions")
    op.drop_index("ix_content_objects_text_hash", table_name="content_objects")
    op.drop_table("content_objects")
