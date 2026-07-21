"""`scan_location` handler: enumerate a source root into catalog observations.

Phase 2 scope: relative path, size, and mtime observations only — hashing and
extraction arrive in Phase 3. Safety rules (state-machine contract sec. 8):

- Observations are staged under ``(job_id, attempt_number, lease_token)``.
- Missing-file reconciliation happens in ONE final fenced transaction, only
  after enumeration completed, the sentinel stayed valid, cancellation was not
  requested, and the worker still owns the unexpired lease.
- An unreachable root or missing/mismatched sentinel is a *transient* error:
  the location is retried and nothing is ever marked missing.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import delete, select, text

from doc_manager.core.config import get_settings
from doc_manager.core.logging import get_logger
from doc_manager.db.models import IngestionJob, ScanObservation, SourceLocation
from doc_manager.db.session import db_now
from doc_manager.domain.enums import JobStatus
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import LeaseLostError, TransientJobError

log = get_logger("doc_manager.jobs.scan_location")

#: Extensions scanned when a location does not restrict them (TECHSTACK sec. 2).
DEFAULT_INCLUDE_EXTENSIONS = frozenset({"pdf", "txt", "md", "csv", "log"})
_STAGE_BATCH_SIZE = 500

_RECONCILE_UPSERT = text(
    """
    INSERT INTO catalog_entries (
        id, source_location_id, relative_path, file_name, extension, state,
        last_observed_size_bytes, last_observed_mtime,
        first_seen_at, last_seen_at, created_at, updated_at
    )
    SELECT gen_random_uuid(), :location_id, o.relative_path, o.file_name,
           o.extension, 'discovered', o.size_bytes, o.mtime,
           clock_timestamp(), clock_timestamp(), clock_timestamp(), clock_timestamp()
    FROM scan_observations o
    WHERE o.job_id = :job_id AND o.attempt_number = :attempt_number
    ON CONFLICT (source_location_id, relative_path) DO UPDATE SET
        last_seen_at = clock_timestamp(),
        last_observed_size_bytes = EXCLUDED.last_observed_size_bytes,
        last_observed_mtime = EXCLUDED.last_observed_mtime,
        state = CASE
            WHEN catalog_entries.state = 'missing' THEN 'discovered'
            ELSE catalog_entries.state
        END,
        missing_since = NULL,
        updated_at = clock_timestamp()
    """
)

_RECONCILE_MISSING = text(
    """
    UPDATE catalog_entries c
    SET state = 'missing', missing_since = clock_timestamp(),
        updated_at = clock_timestamp()
    WHERE c.source_location_id = :location_id
      AND c.state != 'missing'
      AND NOT EXISTS (
          SELECT 1 FROM scan_observations o
          WHERE o.job_id = :job_id
            AND o.attempt_number = :attempt_number
            AND o.relative_path = c.relative_path
      )
    """
)


@dataclass(frozen=True, slots=True)
class _Observed:
    relative_path: str
    file_name: str
    extension: str
    size_bytes: int
    mtime: datetime


class _SentinelCheck:
    """Result of validating the source root and its sentinel file."""

    def __init__(self, observed_sentinel: str | None) -> None:
        self.observed_sentinel = observed_sentinel


def _check_root(location: SourceLocation, sentinel_name: str) -> _SentinelCheck:
    root = Path(location.scan_root)
    if not root.is_dir():
        raise TransientJobError(
            "source_unavailable", f"scan root is not reachable for location {location.id}"
        )
    sentinel = root / sentinel_name
    observed: str | None = None
    if sentinel.is_file():
        observed = sentinel.read_text(encoding="utf-8").strip() or None
    # A provisioned sentinel must be present and must match; otherwise the
    # mapped drive is disconnected or points at the wrong share.
    if location.sentinel_id is not None and observed != location.sentinel_id:
        raise TransientJobError(
            "source_unavailable",
            f"sentinel missing or mismatched for location {location.id}",
        )
    return _SentinelCheck(observed)


def _enumerate(location: SourceLocation) -> list[_Observed]:
    """Blocking filesystem walk; runs in a thread. Symlinks are never followed."""
    root = Path(location.scan_root)
    include = (
        {e.lower() for e in location.include_extensions}
        if location.include_extensions
        else DEFAULT_INCLUDE_EXTENSIONS
    )
    excludes = list(location.exclude_globs)
    observed: list[_Observed] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = PurePosixPath(Path(dirpath).relative_to(root).as_posix())
        # Prune excluded directories before descending into them.
        dirnames[:] = [
            d
            for d in dirnames
            if not _excluded(str(rel_dir / d) if str(rel_dir) != "." else d, excludes)
        ]
        for name in filenames:
            rel_path = str(rel_dir / name) if str(rel_dir) != "." else name
            extension = Path(name).suffix.lower().lstrip(".")
            if extension not in include:
                continue
            if _excluded(rel_path, excludes):
                continue
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            try:
                st = full.stat()
            except OSError:
                # A file that vanished mid-walk is simply not observed; the
                # next scan reconciles it.
                continue
            observed.append(
                _Observed(
                    relative_path=rel_path,
                    file_name=name,
                    extension=extension,
                    size_bytes=st.st_size,
                    mtime=datetime.fromtimestamp(st.st_mtime, tz=UTC),
                )
            )
    return observed


def _excluded(rel_path: str, excludes: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in excludes)


async def handle_scan_location(ctx: JobContext) -> None:
    session = ctx.session
    job = ctx.job
    location = await session.get(SourceLocation, job.source_location_id)
    if location is None:
        raise TransientJobError("source_unavailable", "source location no longer exists")
    if not location.enabled:
        # Disabled between enqueue and claim: succeed as a no-op rather than
        # failing or silently reconciling a location the operator turned off.
        await ctx.engine.complete(
            session, job, worker_id=ctx.worker_id, lease_token=ctx.lease_token
        )
        await session.commit()
        return

    sentinel_name = get_settings().nas_mount_sentinel
    check = _check_root(location, sentinel_name)
    ctx.check_boundary()

    observed = await asyncio.to_thread(_enumerate, location)
    ctx.check_boundary()

    # Stage observations in bounded batches; staging rows are invisible to
    # catalog readers and are keyed to this exact attempt.
    for start in range(0, len(observed), _STAGE_BATCH_SIZE):
        batch = observed[start : start + _STAGE_BATCH_SIZE]
        session.add_all(
            ScanObservation(
                job_id=job.id,
                attempt_number=job.attempt_count,
                lease_token=ctx.lease_token,
                relative_path=item.relative_path,
                file_name=item.file_name,
                extension=item.extension,
                size_bytes=item.size_bytes,
                mtime=item.mtime,
            )
            for item in batch
        )
        await session.commit()
        await ctx.report_progress(
            phase="files_discovered",
            current=min(start + _STAGE_BATCH_SIZE, len(observed)),
            total=len(observed),
            unit="files",
        )
        ctx.check_boundary()

    # Re-verify the source immediately before reconciliation: a root that
    # disappeared mid-scan must never mark unseen files missing.
    _check_root(location, sentinel_name)

    # One fenced reconciliation transaction (auto-begun; committed at the end):
    # only the current unexpired lease may reconcile, and completion publishes
    # in the same transaction (contract sec. 8).
    locked = await session.scalar(
        select(IngestionJob)
        .where(
            IngestionJob.id == job.id,
            IngestionJob.status == JobStatus.running.value,
            IngestionJob.lease_owner == ctx.worker_id,
            IngestionJob.lease_token == ctx.lease_token,
            IngestionJob.lease_expires_at > text("clock_timestamp()"),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:
        raise LeaseLostError(f"job {job.id}: lost lease before reconciliation")
    if locked.cancel_requested_at is not None:
        ctx.cancel_requested = True
        ctx.check_boundary()

    params = {
        "location_id": location.id,
        "job_id": job.id,
        "attempt_number": job.attempt_count,
    }
    await session.execute(_RECONCILE_UPSERT, params)
    result = await session.execute(_RECONCILE_MISSING, params)
    marked_missing = getattr(result, "rowcount", 0)
    now = await db_now(session)
    location.last_successful_scan_at = now
    if location.sentinel_id is None and check.observed_sentinel is not None:
        # First successful scan adopts the observed sentinel; later scans
        # must match it (mapped-drive identity, TECHSTACK sec. 10).
        location.sentinel_id = check.observed_sentinel
    await session.execute(delete(ScanObservation).where(ScanObservation.job_id == job.id))
    await ctx.engine.complete(session, job, worker_id=ctx.worker_id, lease_token=ctx.lease_token)
    await session.commit()
    log.info(
        "scan_completed",
        job_id=str(job.id),
        location_id=str(location.id),
        files_observed=len(observed),
        files_marked_missing=marked_missing,
    )
