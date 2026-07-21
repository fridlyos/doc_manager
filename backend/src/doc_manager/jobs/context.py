"""Per-attempt execution context handed to job handlers.

The worker's heartbeat task flips the flags; handlers call
:meth:`check_boundary` between idempotent steps so cancellation, shutdown, and
lease loss are observed at safe points only (state-machine contract sec. 7).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.db.models import IngestionJob
from doc_manager.jobs.errors import (
    CancelObservedError,
    LeaseLostError,
    ShutdownRequestedError,
)
from doc_manager.jobs.queue import JobEngine


@dataclass
class JobContext:
    """Everything an attempt may use to act with authority.

    `session` is pinned to one database connection for the whole attempt so
    session-level advisory locks survive between transactions.
    """

    session: AsyncSession
    engine: JobEngine
    job: IngestionJob
    worker_id: str
    lease_token: uuid.UUID
    lease_seconds: float
    cancel_requested: bool = False
    shutdown_requested: bool = False
    lease_lost: bool = False
    _progress_sent: int = field(default=0, repr=False)

    def check_boundary(self) -> None:
        """Raise at a safe checkpoint. Lease loss and cancellation beat shutdown."""
        if self.lease_lost:
            raise LeaseLostError(f"job {self.job.id}: lease lost")
        if self.cancel_requested:
            raise CancelObservedError
        if self.shutdown_requested:
            raise ShutdownRequestedError

    async def report_progress(
        self,
        *,
        phase: str,
        current: int,
        total: int | None,
        unit: str,
        message: str | None = None,
    ) -> None:
        """Fenced progress update; a rejected update marks the lease lost."""
        ok = await self.engine.update_progress(
            self.session,
            job_id=self.job.id,
            worker_id=self.worker_id,
            lease_token=self.lease_token,
            phase=phase,
            current=current,
            total=total,
            unit=unit,
            message=message,
        )
        await self.session.commit()
        if not ok:
            self.lease_lost = True
