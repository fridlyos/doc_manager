"""Resource JSON projections. Worker lease internals are never public."""

from __future__ import annotations

from typing import Any

from doc_manager.api.envelope import iso_utc
from doc_manager.db.models import IngestionJob, SourceLocation
from doc_manager.domain.enums import ErrorClass


def location_etag(location: SourceLocation) -> str:
    return f'"location-{location.id}-{location.revision}"'


def serialize_location(location: SourceLocation) -> dict[str, Any]:
    return {
        "id": str(location.id),
        "name": location.name,
        "scan_root": location.scan_root,
        "display_root": location.display_root,
        "path_style": location.path_style,
        "enabled": location.enabled,
        "read_only": location.read_only,
        "external_generation_policy": location.external_generation_policy,
        "include_extensions": list(location.include_extensions),
        "exclude_globs": list(location.exclude_globs),
        "scan_interval_minutes": location.scan_interval_minutes,
        "sentinel_id": location.sentinel_id,
        "last_successful_scan_at": iso_utc(location.last_successful_scan_at),
        "revision": location.revision,
        "created_at": iso_utc(location.created_at),
        "updated_at": iso_utc(location.updated_at),
    }


def serialize_job(job: IngestionJob) -> dict[str, Any]:
    target = None
    if job.source_location_id is not None:
        target = {
            "resource_type": "source_location",
            "resource_id": str(job.source_location_id),
        }
    elif job.catalog_entry_id is not None:
        target = {"resource_type": "catalog_entry", "resource_id": str(job.catalog_entry_id)}
    error = None
    if job.error_code is not None:
        error = {
            "code": job.error_code,
            "message": job.error_message or "",
            "retryable": job.error_class
            in (ErrorClass.transient.value, ErrorClass.internal_unclassified.value),
        }
    return {
        "id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "priority": job.priority,
        "origin": job.origin,
        "target": target,
        "progress": {
            "phase": job.progress_phase,
            "current": job.progress_current or 0,
            "total": job.progress_total,
            "unit": job.progress_unit or "items",
        },
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "requested_at": iso_utc(job.requested_at),
        "started_at": iso_utc(job.started_at),
        "finished_at": iso_utc(job.finished_at),
        "cancel_requested_at": iso_utc(job.cancel_requested_at),
        "retry_of_job_id": str(job.retry_of_job_id) if job.retry_of_job_id else None,
        "root_job_id": str(job.root_job_id) if job.root_job_id else None,
        "error": error,
    }
