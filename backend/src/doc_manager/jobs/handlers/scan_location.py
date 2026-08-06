"""`scan_location` handler: enumerate a source root and reconcile the catalog.

Phase 3.a scope: safe traversal, filtering, SHA-256 hashing, and content-aware
reconciliation (add/change/move/restore/missing). Extraction and vectors arrive
later (3.b+). Safety rules (state-machine contract sec. 8):

- Observations are staged under ``(job_id, attempt_number, lease_token)``.
- Reconciliation happens in ONE final fenced transaction, only after enumeration
  completed, the sentinel stayed valid, cancellation was not requested, and the
  worker still owns the unexpired lease.
- An unreachable root or missing/mismatched sentinel is a *transient* error: the
  location is retried and nothing is ever marked missing.

Hashing is the change authority: a file is hashed when it is new or when its
size/mtime differ from the catalog's last observation; an unchanged file carries
its stored hash forward (fast path). A moved/renamed/restored file is recognized
by its bytes rather than its path.
"""

from __future__ import annotations

import asyncio
import fnmatch
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.core.config import get_settings
from doc_manager.core.hashing import sha256_file
from doc_manager.core.logging import get_logger
from doc_manager.db.models import CatalogEntry, IngestionJob, ScanObservation, SourceLocation
from doc_manager.db.session import db_now
from doc_manager.domain.enums import CatalogEntryState, JobOrigin, JobStatus, JobType
from doc_manager.jobs.context import JobContext
from doc_manager.jobs.errors import LeaseLostError, TransientJobError
from doc_manager.jobs.handlers.reconcile import CatalogRow, ObservedFile, reconcile

log = get_logger("doc_manager.jobs.scan_location")

#: Extensions scanned when a location does not restrict them (TECHSTACK sec. 2).
DEFAULT_INCLUDE_EXTENSIONS = frozenset({"pdf", "txt", "md", "csv", "log"})
#: Directory names never descended into, regardless of a location's globs — VCS
#: metadata and OS/recycle/system folders that are never source documents.
_ALWAYS_EXCLUDE_DIRS = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "$recycle.bin",
        "system volume information",
        ".trash",
        ".trashes",
        "__pycache__",
        "node_modules",
    }
)
_STAGE_BATCH_SIZE = 500

#: (size_bytes, mtime, sha256) keyed by relative path — the prior catalog view
#: used to decide whether a file must be re-hashed.
_PriorSnapshot = dict[str, tuple[int | None, datetime | None, str | None]]


@dataclass(frozen=True, slots=True)
class _Observed:
    relative_path: str
    file_name: str
    extension: str
    size_bytes: int
    mtime: datetime
    sha256: str


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


def _enumerate(location: SourceLocation, prior: _PriorSnapshot) -> list[_Observed]:
    """Blocking filesystem walk + hashing; runs in a thread.

    Symlinks are never followed. A file whose size and mtime match the catalog
    carries its stored hash forward; anything new or changed is hashed now. A
    file that vanishes or cannot be read mid-scan is simply not observed.
    """
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
        # Prune system/VCS dirs and excluded dirs before descending.
        dirnames[:] = [
            d
            for d in dirnames
            if d.lower() not in _ALWAYS_EXCLUDE_DIRS
            and not _excluded(str(rel_dir / d) if str(rel_dir) != "." else d, excludes)
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
                continue
            mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
            sha = _hash_or_carry(full, prior.get(rel_path), st.st_size, mtime)
            if sha is None:
                # Unreadable content (locked/vanished between stat and open);
                # skip it — the next scan reconciles.
                continue
            observed.append(
                _Observed(
                    relative_path=rel_path,
                    file_name=name,
                    extension=extension,
                    size_bytes=st.st_size,
                    mtime=mtime,
                    sha256=sha,
                )
            )
    return observed


def _hash_or_carry(
    full: Path,
    prior: tuple[int | None, datetime | None, str | None] | None,
    size: int,
    mtime: datetime,
) -> str | None:
    """Return the stored hash when size+mtime are unchanged, else re-hash."""
    if prior is not None:
        prior_size, prior_mtime, prior_sha = prior
        if prior_sha is not None and prior_size == size and prior_mtime == mtime:
            return prior_sha
    try:
        return sha256_file(full)
    except OSError:
        return None


def _excluded(rel_path: str, excludes: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in excludes)


async def _load_prior_snapshot(
    session: AsyncSession,
    location_id: object,
) -> _PriorSnapshot:
    rows = await session.execute(
        select(
            CatalogEntry.relative_path,
            CatalogEntry.last_observed_size_bytes,
            CatalogEntry.last_observed_mtime,
            CatalogEntry.sha256,
        ).where(CatalogEntry.source_location_id == location_id)
    )
    return {path: (size, mtime, sha) for path, size, mtime, sha in rows}


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

    prior = await _load_prior_snapshot(session, location.id)
    observed = await asyncio.to_thread(_enumerate, location, prior)
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
                sha256=item.sha256,
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

    counts = await _apply_reconciliation(session, location, job)
    enqueued = await _enqueue_indexing(ctx, location)
    if counts.get("missing"):
        # Deletions detected: retire their now-orphaned vectors so the store
        # converges after the scan (Phase 6.d). Deduped across scans.
        await ctx.engine.enqueue(
            ctx.session,
            job_type=JobType.remove_stale_vectors,
            payload={"version": 1},
            origin=JobOrigin.handler,
            dedupe_key="remove_stale_vectors",
            max_attempts=get_settings().job_max_attempts,
            actor="scan",
        )
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
        index_jobs_enqueued=enqueued,
        **counts,
    )


async def _enqueue_indexing(ctx: JobContext, location: SourceLocation) -> int:
    """Enqueue an index_file job per entry still needing indexing.

    Runs in the scan's final transaction so indexing is queued atomically with
    reconciliation. Deduped per catalog entry, so repeated scans coalesce onto
    the open index job instead of piling up.
    """
    await ctx.session.flush()  # new/updated entries must have ids + states set
    entry_ids = (
        await ctx.session.scalars(
            select(CatalogEntry.id).where(
                CatalogEntry.source_location_id == location.id,
                CatalogEntry.state == CatalogEntryState.discovered.value,
            )
        )
    ).all()
    enqueued = 0
    for entry_id in entry_ids:
        _, coalesced = await ctx.engine.enqueue(
            ctx.session,
            job_type=JobType.index_file,
            payload={"version": 1, "catalog_entry_id": str(entry_id)},
            origin=JobOrigin.handler,
            catalog_entry_id=entry_id,
            dedupe_key=f"index:{entry_id}",
            max_attempts=get_settings().job_max_attempts,
            actor="scan",
        )
        if not coalesced:
            enqueued += 1
    return enqueued


async def _apply_reconciliation(
    session: AsyncSession, location: SourceLocation, job: IngestionJob
) -> dict[str, int]:
    """Fold staged observations into the catalog and return transition counts."""
    entries = (
        await session.scalars(
            select(CatalogEntry).where(CatalogEntry.source_location_id == location.id)
        )
    ).all()
    by_id = {entry.id: entry for entry in entries}
    catalog_rows = [
        CatalogRow(
            id=entry.id,
            relative_path=entry.relative_path,
            state=entry.state,
            size_bytes=entry.last_observed_size_bytes,
            mtime=entry.last_observed_mtime,
            sha256=entry.sha256,
        )
        for entry in entries
    ]
    staged = await session.scalars(
        select(ScanObservation).where(
            ScanObservation.job_id == job.id,
            ScanObservation.attempt_number == job.attempt_count,
        )
    )
    observations = [
        ObservedFile(
            relative_path=row.relative_path,
            file_name=row.file_name,
            extension=row.extension,
            size_bytes=row.size_bytes,
            mtime=row.mtime,
            sha256=row.sha256,
        )
        for row in staged
    ]

    plan = reconcile(catalog_rows, observations)
    now = await db_now(session)

    for add in plan.adds:
        obs = add.observed
        session.add(
            CatalogEntry(
                source_location_id=location.id,
                relative_path=obs.relative_path,
                file_name=obs.file_name,
                extension=obs.extension,
                state=CatalogEntryState.discovered.value,
                last_observed_size_bytes=obs.size_bytes,
                last_observed_mtime=obs.mtime,
                sha256=obs.sha256,
            )
        )
    for upd in plan.updates:
        entry = by_id[upd.entry_id]
        obs = upd.observed
        # A move retargets the path in place, preserving indexed state/content.
        entry.relative_path = obs.relative_path
        entry.file_name = obs.file_name
        entry.extension = obs.extension
        entry.last_observed_size_bytes = obs.size_bytes
        entry.last_observed_mtime = obs.mtime
        entry.sha256 = obs.sha256
        entry.state = upd.state
        entry.last_seen_at = now
        if upd.clear_missing:
            entry.missing_since = None
    for mark in plan.missing:
        entry = by_id[mark.entry_id]
        entry.state = CatalogEntryState.missing.value
        entry.missing_since = now

    return plan.counts()
