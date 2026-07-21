"""Background worker: claim loop, heartbeat, reaper, and scheduler.

Implements the runtime behavior of the job state-machine contract (secs. 5 and
11): stale-lease reaping before the first claim, heartbeats on an independent
connection, cooperative cancellation, and graceful shutdown that releases work
to immediate ``retry_wait`` instead of forging terminal states.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import socket
import uuid

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from doc_manager import __version__
from doc_manager.core.config import Settings, get_settings
from doc_manager.core.logging import configure_logging, get_logger
from doc_manager.db.models import IngestionJob
from doc_manager.db.session import create_engine, create_session_factory
from doc_manager.domain.enums import ErrorClass, JobType
from doc_manager.jobs import scheduler
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import (
    CancelObservedError,
    LeaseLostError,
    PermanentJobError,
    ShutdownRequestedError,
    TransientJobError,
)
from doc_manager.jobs.handlers import HANDLERS
from doc_manager.jobs.queue import JobEngine

log = get_logger("doc_manager.worker")


class WorkerRunner:
    def __init__(self, settings: Settings, db_engine: AsyncEngine) -> None:
        self.settings = settings
        self.db_engine = db_engine
        self.session_factory = create_session_factory(db_engine)
        self.job_engine = JobEngine(
            base_delay_seconds=settings.job_retry_base_delay_seconds,
            max_delay_seconds=settings.job_retry_max_delay_seconds,
        )
        # New process-instance identity every startup (contract sec. 5).
        self.worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
        self.stop = asyncio.Event()

    # --------------------------------------------------------------- lifecycle

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.stop.set)
        log.info(
            "worker_startup",
            version=__version__,
            worker_id=self.worker_id,
            concurrency=self.settings.worker_concurrency,
            lease_seconds=self.settings.job_lease_seconds,
        )
        # Reap stale leases before claiming anything (contract sec. 11).
        async with self.session_factory() as session:
            await self.job_engine.reap_expired(session, actor=f"{self.worker_id}:startup")

        tasks = [
            asyncio.create_task(self._claim_loop(n), name=f"claim-{n}")
            for n in range(max(1, self.settings.worker_concurrency))
        ]
        tasks.append(asyncio.create_task(self._reaper_loop(), name="reaper"))
        tasks.append(asyncio.create_task(self._scheduler_loop(), name="scheduler"))
        await self.stop.wait()
        log.info("worker_draining", worker_id=self.worker_id)
        done, pending = await asyncio.wait(
            tasks, timeout=self.settings.worker_shutdown_grace_seconds
        )
        for task in pending:
            # Past the grace deadline: cancel without forging terminal states;
            # leases expire and the reaper recovers the jobs.
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        log.info("worker_shutdown", worker_id=self.worker_id)

    async def _sleep_unless_stopping(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), timeout=seconds)

    # -------------------------------------------------------------- background

    async def _reaper_loop(self) -> None:
        while not self.stop.is_set():
            try:
                async with self.session_factory() as session:
                    reaped = await self.job_engine.reap_expired(
                        session, actor=f"{self.worker_id}:reaper"
                    )
                if reaped:
                    log.info("reaped_expired_leases", count=reaped)
            except Exception:
                log.exception("reaper_tick_failed")
            await self._sleep_unless_stopping(self.settings.reaper_interval_seconds)

    async def _scheduler_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await scheduler.tick(
                    self.session_factory,
                    self.job_engine,
                    max_attempts=self.settings.job_max_attempts,
                )
            except Exception:
                log.exception("scheduler_tick_failed")
            await self._sleep_unless_stopping(self.settings.scheduler_interval_seconds)

    # ------------------------------------------------------------------ claims

    async def _claim_loop(self, slot: int) -> None:
        worker_id = f"{self.worker_id}:{slot}"
        registered = tuple(HANDLERS.keys())
        skip_ids: tuple[uuid.UUID, ...] = ()
        while not self.stop.is_set():
            try:
                ran = await self._claim_and_run_one(worker_id, registered, skip_ids)
            except Exception:
                log.exception("claim_loop_iteration_failed", worker=worker_id)
                ran = None
            if isinstance(ran, uuid.UUID):
                skip_ids = (*skip_ids, ran)  # advisory lock busy: skip this round
                continue
            skip_ids = ()
            if not ran:
                await self._sleep_unless_stopping(self.settings.job_poll_interval_seconds)

    async def _claim_and_run_one(
        self,
        worker_id: str,
        job_types: tuple[JobType, ...],
        skip_ids: tuple[uuid.UUID, ...],
    ) -> bool | uuid.UUID:
        """Claim and execute one job. Returns False (idle), True (ran a job),
        or the job id whose location advisory lock was busy."""
        # One pinned connection per attempt so session-level advisory locks
        # survive across the handler's transactions.
        async with self.db_engine.connect() as conn:
            session = AsyncSession(bind=conn, expire_on_commit=False)
            try:
                claim = await self.job_engine.claim_next(
                    session,
                    worker_id=worker_id,
                    lease_seconds=self.settings.job_lease_seconds,
                    skip_ids=skip_ids,
                    job_types=job_types,
                )
                if claim.busy_job_id is not None:
                    return claim.busy_job_id
                if claim.job is None:
                    return False
                await self._run_attempt(session, claim.job, worker_id)
                return True
            finally:
                await session.close()

    async def _run_attempt(self, session: AsyncSession, job: IngestionJob, worker_id: str) -> None:
        assert job.lease_token is not None
        ctx = JobContext(
            session=session,
            engine=self.job_engine,
            job=job,
            worker_id=worker_id,
            lease_token=job.lease_token,
            lease_seconds=self.settings.job_lease_seconds,
        )
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(ctx), name=f"heartbeat-{job.id}")
        handler = HANDLERS[JobType(job.job_type)]
        log.info(
            "attempt_started",
            job_id=str(job.id),
            job_type=job.job_type,
            attempt=job.attempt_count,
            worker=worker_id,
        )
        try:
            await handler(ctx)
        except LeaseLostError:
            log.warning("attempt_lost_lease", job_id=str(job.id), worker=worker_id)
        except CancelObservedError:
            await self._finalize(session, ctx, "cancel")
        except ShutdownRequestedError:
            await self._finalize(session, ctx, "release")
        except TransientJobError as exc:
            await self._finalize(session, ctx, "transient", exc.code, exc.message)
        except PermanentJobError as exc:
            await self._finalize(session, ctx, "permanent", exc.code, exc.message)
        except Exception:
            # Unknown errors retry only within the bounded attempt budget and
            # are never classified permanent silently (contract sec. 6).
            log.exception("attempt_unclassified_error", job_id=str(job.id))
            await self._finalize(
                session,
                ctx,
                "unclassified",
                "internal_unclassified",
                "unclassified internal error; see worker diagnostics",
            )
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            if job.job_type == JobType.scan_location.value and job.source_location_id is not None:
                with contextlib.suppress(Exception):
                    await self.job_engine.release_scan_lock(session, job.source_location_id)

    async def _finalize(
        self,
        session: AsyncSession,
        ctx: JobContext,
        outcome: str,
        code: str = "",
        message: str = "",
    ) -> None:
        """Apply the attempt outcome in a fresh fenced transaction."""
        engine = self.job_engine
        try:
            if session.in_transaction():
                await session.rollback()
            async with session.begin():
                if outcome == "cancel":
                    await engine.acknowledge_cancel(
                        session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
                    )
                elif outcome == "release":
                    await engine.release_for_shutdown(
                        session, ctx.job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
                    )
                elif outcome == "permanent":
                    await engine.fail_permanent(
                        session,
                        ctx.job,
                        worker_id=ctx.worker_id,
                        lease_token=ctx.lease_token,
                        code=code,
                        message=message,
                    )
                elif outcome == "unclassified":
                    await engine.retry_transient(
                        session,
                        ctx.job,
                        worker_id=ctx.worker_id,
                        lease_token=ctx.lease_token,
                        code=code,
                        message=message,
                        error_class=ErrorClass.internal_unclassified,
                    )
                else:
                    await engine.retry_transient(
                        session,
                        ctx.job,
                        worker_id=ctx.worker_id,
                        lease_token=ctx.lease_token,
                        code=code,
                        message=message,
                    )
        except LeaseLostError:
            log.warning("finalize_lost_lease", job_id=str(ctx.job.id), outcome=outcome)

    async def _heartbeat_loop(self, ctx: JobContext) -> None:
        """Renew the lease on an independent connection (contract sec. 5)."""
        interval = min(
            self.settings.job_heartbeat_seconds, max(1, self.settings.job_lease_seconds // 3)
        )
        while True:
            await asyncio.sleep(interval)
            try:
                async with self.session_factory() as hb_session:
                    result = await self.job_engine.heartbeat(
                        hb_session,
                        job_id=ctx.job.id,
                        worker_id=ctx.worker_id,
                        lease_token=ctx.lease_token,
                        lease_seconds=ctx.lease_seconds,
                    )
            except Exception:
                log.exception("heartbeat_failed", job_id=str(ctx.job.id))
                continue
            if not result.alive:
                ctx.lease_lost = True
                return
            if result.cancel_requested:
                ctx.cancel_requested = True
            if self.stop.is_set():
                ctx.shutdown_requested = True


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.env.value != "development")

    async def _main() -> None:
        db_engine = create_engine(settings)
        try:
            await WorkerRunner(settings, db_engine).run()
        finally:
            await db_engine.dispose()

    asyncio.run(_main())


if __name__ == "__main__":
    run()
