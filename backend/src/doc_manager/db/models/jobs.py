"""Durable job queue tables (state-machine contract sections 4 and 8).

PostgreSQL is the only authority for whether work is pending, leased,
retryable, cancelled, or complete. Database CHECK constraints enforce the
lease/terminal invariants where practical; partial unique indexes enforce
one-open-scan-per-location and one-open-manual-retry-child rules under
concurrency.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from doc_manager.db.models.base import Base
from doc_manager.domain.enums import JobOrigin, JobStatus

_OPEN = "('queued', 'running', 'retry_wait')"
_TERMINAL = "('succeeded', 'failed', 'cancelled', 'superseded')"


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            f"status IN {_OPEN} OR status IN {_TERMINAL}",
            name="status_known",
        ),
        # Lease fields are non-null exactly while running (contract sec. 4).
        CheckConstraint(
            "(status = 'running') = (lease_owner IS NOT NULL AND lease_token IS NOT NULL"
            " AND lease_expires_at IS NOT NULL)",
            name="lease_only_while_running",
        ),
        CheckConstraint(
            f"(status IN {_TERMINAL}) = (finished_at IS NOT NULL)",
            name="finished_only_when_terminal",
        ),
        CheckConstraint("attempt_count >= 0 AND max_attempts >= 1", name="attempt_bounds"),
        # At most one open scan per location (contract sec. 8).
        Index(
            "one_open_scan_per_location",
            "source_location_id",
            unique=True,
            postgresql_where=text(f"job_type = 'scan_location' AND status IN {_OPEN}"),
        ),
        # At most one open manual-retry child per source job (contract sec. 7).
        Index(
            "one_open_manual_retry_per_job",
            "retry_of_job_id",
            unique=True,
            postgresql_where=text(f"origin = 'manual_retry' AND status IN {_OPEN}"),
        ),
        # Enqueue-level dedupe for handler/scheduler-originated work.
        Index(
            "one_open_job_per_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text(f"dedupe_key IS NOT NULL AND status IN {_OPEN}"),
        ),
        # Claim scan: due open jobs by priority and age.
        Index(
            "ix_ingestion_jobs_claim_order",
            "status",
            "available_at",
            "priority",
            "requested_at",
        ),
    )

    # --- Identity and routing ---
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(50))
    priority: Mapped[int] = mapped_column(BigInteger, default=0)
    source_location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_locations.id", ondelete="SET NULL"), default=None
    )
    catalog_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalog_entries.id", ondelete="SET NULL"), default=None
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(default=dict)

    # --- State and timing (PostgreSQL time only) ---
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.queued.value)
    requested_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    available_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    # --- Attempts ---
    attempt_count: Mapped[int] = mapped_column(BigInteger, default=0)
    max_attempts: Mapped[int] = mapped_column(BigInteger, default=3)

    # --- Lease fencing ---
    lease_owner: Mapped[str | None] = mapped_column(String(200), default=None)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    heartbeat_at: Mapped[datetime | None] = mapped_column(default=None)

    # --- Progress (observability, not correctness) ---
    progress_phase: Mapped[str | None] = mapped_column(String(50), default=None)
    progress_current: Mapped[int | None] = mapped_column(BigInteger, default=None)
    progress_total: Mapped[int | None] = mapped_column(BigInteger, default=None)
    progress_unit: Mapped[str | None] = mapped_column(String(50), default=None)
    progress_message: Mapped[str | None] = mapped_column(String(500), default=None)
    progress_updated_at: Mapped[datetime | None] = mapped_column(default=None)

    # --- Error (sanitized; never document text or tracebacks) ---
    error_class: Mapped[str | None] = mapped_column(String(30), default=None)
    error_code: Mapped[str | None] = mapped_column(String(100), default=None)
    error_message: Mapped[str | None] = mapped_column(String(1000), default=None)
    error_details_json: Mapped[dict[str, Any] | None] = mapped_column(default=None)

    # --- Cancellation intent (not a stored status) ---
    cancel_requested_at: Mapped[datetime | None] = mapped_column(default=None)
    cancel_requested_by: Mapped[str | None] = mapped_column(String(100), default=None)

    # --- Lineage ---
    retry_of_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="SET NULL"), default=None
    )
    root_job_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    replacement_job_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    origin: Mapped[str] = mapped_column(String(20), default=JobOrigin.api.value)

    # --- Enqueue idempotency ---
    dedupe_key: Mapped[str | None] = mapped_column(String(300), default=None)
    request_key: Mapped[str | None] = mapped_column(String(128), default=None)

    # --- Event ordering ---
    last_event_sequence: Mapped[int] = mapped_column(BigInteger, default=0)


class IngestionJobAttempt(Base):
    __tablename__ = "ingestion_job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_number"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="CASCADE"))
    attempt_number: Mapped[int] = mapped_column(BigInteger)
    worker_id: Mapped[str] = mapped_column(String(200))
    lease_token: Mapped[uuid.UUID] = mapped_column()
    started_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    outcome: Mapped[str | None] = mapped_column(String(30), default=None)
    error_class: Mapped[str | None] = mapped_column(String(30), default=None)
    error_code: Mapped[str | None] = mapped_column(String(100), default=None)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(default=None)


class JobEvent(Base):
    __tablename__ = "job_events"
    __table_args__ = (UniqueConstraint("job_id", "sequence_number"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="CASCADE"))
    sequence_number: Mapped[int] = mapped_column(BigInteger)
    attempt_number: Mapped[int | None] = mapped_column(BigInteger, default=None)
    event_type: Mapped[str] = mapped_column(String(50))
    level: Mapped[str] = mapped_column(String(10), default="info")
    actor: Mapped[str | None] = mapped_column(String(200), default=None)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    message: Mapped[str | None] = mapped_column(String(500), default=None)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(default=None)


class JobCheckpoint(Base):
    __tablename__ = "job_checkpoints"
    __table_args__ = (UniqueConstraint("job_id", "checkpoint_name", "input_fingerprint"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="CASCADE"))
    checkpoint_name: Mapped[str] = mapped_column(String(100))
    input_fingerprint: Mapped[str] = mapped_column(String(200))
    # 'started' or 'completed'; promoted monotonically, never rewritten.
    status: Mapped[str] = mapped_column(String(20), default="started")
    attempt_number: Mapped[int] = mapped_column(BigInteger)
    result_identity: Mapped[str | None] = mapped_column(String(500), default=None)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    started_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())
    completed_at: Mapped[datetime | None] = mapped_column(default=None)


class IdempotencyRecord(Base):
    """HTTP `Idempotency-Key` reservations (API contract section 6.1).

    `scope` binds the key to method + canonical route template + key value.
    The reservation insert and durable job creation share one transaction.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("scope"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(500))
    fingerprint: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.clock_timestamp())


class SchedulerState(Base):
    """Single-row bookkeeping so scheduler ticks survive restarts."""

    __tablename__ = "scheduler_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=1)
    last_tick_at: Mapped[datetime | None] = mapped_column(default=None)
