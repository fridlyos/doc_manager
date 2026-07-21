"""phase 3.a content hash

Adds SHA-256 content hashing to the scanner: a nullable ``sha256`` on
``catalog_entries`` (the current content authority; NULL for entries observed
before hashing landed) and a required ``sha256`` on ``scan_observations``
(carried on the unchanged fast path or freshly computed).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-21 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_entries",
        sa.Column("sha256", sa.String(length=64), nullable=True),
    )
    # Existing staging rows (if any) predate hashing; a scan clears staging each
    # run, so a server_default backfill is only to satisfy NOT NULL at add time.
    op.add_column(
        "scan_observations",
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
    )
    op.alter_column("scan_observations", "sha256", server_default=None)


def downgrade() -> None:
    op.drop_column("scan_observations", "sha256")
    op.drop_column("catalog_entries", "sha256")
