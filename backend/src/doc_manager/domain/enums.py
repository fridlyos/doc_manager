"""Domain enums shared by the database models, job engine, API, and worker.

Values are stored in PostgreSQL as plain text (no native enum types) so adding
a member is an additive application change, not a database migration. The
spellings are normative API contract values (docs/api/contracts.md section 2.1)
and state-machine states (docs/architecture/ingestion-job-state-machine.md).
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    retry_wait = "retry_wait"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    superseded = "superseded"


#: States a worker may claim from.
CLAIMABLE_STATUSES = frozenset({JobStatus.queued, JobStatus.retry_wait})
#: Open (non-terminal) states, used by partial-uniqueness rules.
OPEN_STATUSES = frozenset({JobStatus.queued, JobStatus.running, JobStatus.retry_wait})
#: Terminal states; rows in these states never transition again.
TERMINAL_STATUSES = frozenset(
    {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled, JobStatus.superseded}
)


class JobType(StrEnum):
    scan_location = "scan_location"
    index_file = "index_file"
    remove_stale_vectors = "remove_stale_vectors"
    reindex_document = "reindex_document"
    reindex_all_for_profile = "reindex_all_for_profile"
    build_duplicate_report = "build_duplicate_report"
    build_sync_plan = "build_sync_plan"
    catalog_consistency_check = "catalog_consistency_check"


class JobOrigin(StrEnum):
    api = "api"
    scheduler = "scheduler"
    handler = "handler"
    manual_retry = "manual_retry"
    maintenance = "maintenance"


class AttemptOutcome(StrEnum):
    succeeded = "succeeded"
    transient_error = "transient_error"
    permanent_error = "permanent_error"
    cancelled = "cancelled"
    superseded = "superseded"
    lease_expired = "lease_expired"
    shutdown_released = "shutdown_released"


class ErrorClass(StrEnum):
    transient = "transient"
    permanent = "permanent"
    superseded = "superseded"
    cancelled = "cancelled"
    internal_unclassified = "internal_unclassified"


class CatalogEntryState(StrEnum):
    discovered = "discovered"
    queued = "queued"
    indexed = "indexed"
    failed = "failed"
    missing = "missing"
    unsupported = "unsupported"


class ExtractionStatus(StrEnum):
    pending = "pending"
    extracted = "extracted"
    failed = "failed"
    unsupported = "unsupported"


class ExternalGenerationPolicy(StrEnum):
    deny = "deny"
    allow = "allow"


class SyncPlanStatus(StrEnum):
    building = "building"
    ready = "ready"
    failed = "failed"


class PathStyle(StrEnum):
    linux = "linux"
    windows = "windows"
    unc = "unc"
    mapped_drive = "mapped_drive"
