"""End-to-end smoke coverage for the worker claim-and-execute path."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from doc_manager.core.config import Settings
from doc_manager.db.models import (
    CatalogEntry,
    IngestionJob,
    IngestionJobAttempt,
    JobEvent,
    SourceLocation,
)
from doc_manager.domain.enums import AttemptOutcome, JobOrigin, JobStatus, JobType
from doc_manager.jobs.worker import WorkerRunner

pytestmark = pytest.mark.usefixtures("pg_url")


async def test_worker_claims_and_completes_scan(
    tmp_path: Path,
    pg_url: str,
    db_engine: AsyncEngine,
) -> None:
    """A durable queued scan reaches succeeded through the real worker entry point."""
    (tmp_path / "readme.md").write_text("# worker smoke\n", encoding="utf-8")
    (tmp_path / ".docman-source-id").write_text("worker-smoke\n", encoding="utf-8")

    runner = WorkerRunner(
        Settings(
            env="test",
            database_url=pg_url,
            allowed_source_roots=str(tmp_path),
        ),
        db_engine,
    )
    async with runner.session_factory() as session:
        location = SourceLocation(
            name="worker-smoke",
            scan_root=str(tmp_path),
            display_root=str(tmp_path),
        )
        session.add(location)
        await session.flush()
        job, coalesced = await runner.job_engine.enqueue(
            session,
            job_type=JobType.scan_location,
            payload={"version": 1, "source_location_id": str(location.id)},
            origin=JobOrigin.api,
            source_location_id=location.id,
            max_attempts=runner.settings.job_max_attempts,
            actor="test",
        )
        await session.commit()

    assert coalesced is False
    ran = await runner._claim_and_run_one(
        "worker-smoke:0",
        (JobType.scan_location,),
        (),
    )
    assert ran is True

    async with runner.session_factory() as session:
        completed = await session.get(IngestionJob, job.id)
        assert completed is not None
        assert completed.status == JobStatus.succeeded.value
        assert completed.attempt_count == 1
        assert completed.finished_at is not None
        assert completed.lease_owner is None
        assert completed.lease_token is None

        attempt = await session.scalar(
            select(IngestionJobAttempt).where(IngestionJobAttempt.job_id == job.id)
        )
        assert attempt is not None
        assert attempt.outcome == AttemptOutcome.succeeded.value
        assert attempt.finished_at is not None

        event_types = (
            await session.scalars(
                select(JobEvent.event_type)
                .where(JobEvent.job_id == job.id)
                .order_by(JobEvent.sequence_number)
            )
        ).all()
        assert event_types == ["job_enqueued", "attempt_started", "job_succeeded"]

        entries = (
            await session.scalars(
                select(CatalogEntry).where(CatalogEntry.source_location_id == location.id)
            )
        ).all()
        assert [entry.relative_path for entry in entries] == ["readme.md"]
