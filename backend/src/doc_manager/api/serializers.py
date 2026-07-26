"""Resource JSON projections. Worker lease internals are never public."""

from __future__ import annotations

from typing import Any

from doc_manager.api.envelope import iso_utc
from doc_manager.core.display import display_path
from doc_manager.db.models import (
    CatalogEntry,
    ContentObject,
    DuplicateGroup,
    DuplicateMember,
    FileVersion,
    IngestionJob,
    SourceLocation,
)
from doc_manager.domain.enums import ErrorClass
from doc_manager.retrieval import SearchResult


def location_etag(location: SourceLocation) -> str:
    return f'"location-{location.id}-{location.revision}"'


def _display_path(location: SourceLocation, relative_path: str) -> str:
    return display_path(location.path_style, location.display_root, relative_path)


def serialize_document(
    entry: CatalogEntry,
    location: SourceLocation,
    version: FileVersion | None,
    content: ContentObject | None,
) -> dict[str, Any]:
    """Project a catalog entry (plus its current file version) as a document.

    Per-document extraction outcome — status, error code/message, and content
    statistics — is surfaced here so the error queue and detail view can isolate
    failures to a single document (Phase 3.e exit criterion).
    """
    error = None
    extraction_status = None
    content_object = None
    if version is not None:
        extraction_status = version.extraction_status
        if version.error_code is not None:
            error = {"code": version.error_code, "message": version.error_message or ""}
        if content is not None:
            content_object = {
                "id": str(content.id),
                "page_count": content.page_count,
                "character_count": content.character_count,
                "extractor_name": content.extractor_name,
                "extractor_version": content.extractor_version,
                "normalization_version": content.normalization_version,
            }
    return {
        "id": str(entry.id),
        "source_location_id": str(entry.source_location_id),
        "display_path": _display_path(location, entry.relative_path),
        "file_name": entry.file_name,
        "extension": entry.extension,
        "mime_type": entry.mime_type,
        "state": entry.state,
        "size_bytes": entry.last_observed_size_bytes,
        "modified_at": iso_utc(entry.last_observed_mtime),
        "sha256": entry.sha256,
        "extraction_status": extraction_status,
        "error": error,
        "content_object": content_object,
        "first_seen_at": iso_utc(entry.first_seen_at),
        "last_seen_at": iso_utc(entry.last_seen_at),
        "missing_since": iso_utc(entry.missing_since),
        "indexed_at": iso_utc(version.indexed_at) if version is not None else None,
        "created_at": iso_utc(entry.created_at),
        "updated_at": iso_utc(entry.updated_at),
    }


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


def serialize_citation(citation: Any) -> dict[str, Any]:
    return {
        "citation_id": citation.citation_id,
        "ordinal": citation.ordinal,
        "chunk_id": citation.chunk_id,
        "page_start": citation.page_start,
        "page_end": citation.page_end,
        "snippet": citation.snippet,
        "similarity_score": citation.similarity_score,
        "availability": citation.availability,
        "paths": [
            {
                "catalog_entry_id": path.catalog_entry_id,
                "source_location_id": path.source_location_id,
                "display_path": path.display_path,
                "state": path.state,
                "is_primary": path.is_primary,
            }
            for path in citation.paths
        ],
    }


def serialize_ask_result(result: Any) -> dict[str, Any]:
    """Project an ``AskResult`` into the contract §8.2 discriminated result."""
    usage = None
    if result.usage is not None and result.invoked:
        usage = {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
        }
    body: dict[str, Any] = {
        "id": result.id,
        "status": result.status,
        "answer": result.answer,
        "answer_format": "markdown",
        "provider": {
            "provider_id": result.provider_id,
            "model_id": result.model_id,
            "data_boundary": result.data_boundary.classification,
            "invoked": result.invoked,
        },
        "data_boundary": result.data_boundary.as_dict(),
        "retrieval": {
            "candidate_count": result.candidate_count,
            "selected_evidence_count": result.selected_count,
            "sufficient": result.sufficient,
        },
        "citations": [serialize_citation(c) for c in result.citations],
        "finish_reason": result.finish_reason,
        "usage": usage,
        "timing": {
            "retrieval_ms": result.retrieval_ms,
            "generation_ms": result.generation_ms,
            "total_ms": result.total_ms,
        },
        "warnings": list(result.warnings),
    }
    if result.confirmation is not None:
        body["confirmation"] = result.confirmation
    return body


def serialize_duplicate_member(member: DuplicateMember) -> dict[str, Any]:
    return {
        "catalog_entry_id": str(member.catalog_entry_id),
        "source_location_id": str(member.source_location_id),
        "display_path": member.display_path,
        "state": member.state,
        "sha256": member.sha256,
    }


def serialize_duplicate_group(
    group: DuplicateGroup, members: list[DuplicateMember] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": str(group.id),
        "kind": group.kind,
        "group_hash": group.group_hash,
        "member_count": group.member_count,
        "built_at": iso_utc(group.built_at),
    }
    if members is not None:
        body["members"] = [serialize_duplicate_member(m) for m in members]
    return body


def serialize_search_result(result: SearchResult) -> dict[str, Any]:
    """Project a retrieval hit. ``similarity_score`` is comparable only within one
    embedding profile; paths are the current server-resolved display paths."""
    return {
        "chunk_id": result.chunk_id,
        "content_object_id": result.content_object_id,
        "similarity_score": result.score,
        "page_start": result.page_start,
        "page_end": result.page_end,
        "snippet": result.snippet,
        "availability": result.availability,
        "paths": [
            {
                "catalog_entry_id": path.catalog_entry_id,
                "source_location_id": path.source_location_id,
                "display_path": path.display_path,
                "state": path.state,
                "is_primary": path.is_primary,
            }
            for path in result.paths
        ],
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
