"""phase 9.a scan progress detail

Adds ``ingestion_jobs.progress_detail_json`` — a nullable JSONB breakdown for a
scan's live progress (discovered/scanned/target + reconcile counts). Observability
only; never gates job completion. Also indexes ``root_job_id`` so a scan's
``index_file`` children can be aggregated for the progress view.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-03 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column("progress_detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_ingestion_jobs_root_job_id", "ingestion_jobs", ["root_job_id"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_root_job_id", table_name="ingestion_jobs")
    op.drop_column("ingestion_jobs", "progress_detail_json")
