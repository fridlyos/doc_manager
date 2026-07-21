"""Source-location resource routes (API contract secs. 3, 5-7).

Mutations use optimistic concurrency: GET returns a strong ETag; PATCH and
state-changing actions require If-Match. Scan requests are durable jobs behind
Idempotency-Key with domain-level coalescing.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from doc_manager.api.dependencies import (
    get_session,
    get_settings_dep,
    parse_uuid,
    request_fingerprint,
    require_idempotency_key,
    reserve_idempotency,
)
from doc_manager.api.envelope import collection_envelope, envelope
from doc_manager.api.errors import Problem
from doc_manager.api.pagination import (
    decode_cursor,
    encode_cursor,
    parse_limit,
    parse_sort,
)
from doc_manager.api.serializers import location_etag, serialize_job, serialize_location
from doc_manager.core.config import Settings
from doc_manager.core.fs_picker import (
    detect_path_style,
    native_picker_available,
    pick_folder_native,
)
from doc_manager.core.preflight import check_source_sentinel
from doc_manager.db.models import IngestionJob, SourceLocation
from doc_manager.domain.enums import (
    OPEN_STATUSES,
    ExternalGenerationPolicy,
    JobOrigin,
    JobType,
    PathStyle,
)
from doc_manager.jobs.queue import JobEngine

router = APIRouter(prefix="/locations", tags=["locations"])

_SORTS = {
    "-updated_at": (SourceLocation.updated_at, True),
    "updated_at": (SourceLocation.updated_at, False),
    "name": (SourceLocation.name, False),
    "-name": (SourceLocation.name, True),
}


class LocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    scan_root: str = Field(min_length=1)
    display_root: str | None = None
    path_style: PathStyle = PathStyle.linux
    enabled: bool = True
    external_generation_policy: ExternalGenerationPolicy = ExternalGenerationPolicy.deny
    include_extensions: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    scan_interval_minutes: int | None = Field(default=None, ge=1)

    @field_validator("include_extensions")
    @classmethod
    def _normalize_extensions(cls, value: list[str]) -> list[str]:
        normalized = [ext.strip().lstrip(".").lower() for ext in value]
        if any(not ext for ext in normalized):
            raise ValueError("extensions must be non-empty")
        return normalized


class LocationPatch(BaseModel):
    """merge-patch body: omitted means unchanged (contract section 2.1)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    display_root: str | None = None
    enabled: bool | None = None
    external_generation_policy: ExternalGenerationPolicy | None = None
    include_extensions: list[str] | None = None
    exclude_globs: list[str] | None = None
    scan_interval_minutes: int | None = Field(default=None, ge=1)


_WINDOWS_STYLES = frozenset({PathStyle.windows, PathStyle.mapped_drive, PathStyle.unc})
_DRIVE_LETTER = re.compile(r"[A-Za-z]:")


def _pure_path(raw: str, path_style: PathStyle) -> PurePath:
    """Interpret a stored/user path under its declared style, not the host OS.

    PureWindowsPath comparisons are case-insensitive on any host, matching
    Windows filesystem semantics for mapped drives and UNC shares.
    """
    if PathStyle(path_style) in _WINDOWS_STYLES:
        return PureWindowsPath(raw)
    return PurePosixPath(raw)


def _allowed_roots(settings: Settings) -> list[PurePath]:
    """Configured roots, each parsed under the style implied by its own shape.

    A container mount like ``/hostfs`` is a posix path even when it exposes a
    Windows drive, so the style must come from the string itself, not from a
    caller-supplied profile/style that may contradict it.
    """
    roots: list[PurePath] = []
    for entry in settings.allowed_source_roots.split(","):
        entry = entry.strip()
        if entry:
            roots.append(_pure_path(entry, PathStyle(detect_path_style(entry))))
    return roots


def _validate_scan_root(
    settings: Settings, scan_root: str, path_style: PathStyle, *, field_name: str = "scan_root"
) -> PurePath:
    path = _pure_path(scan_root, path_style)
    if not path.is_absolute() or ".." in path.parts:
        raise Problem(
            422, "validation_failed", f"{field_name} must be an absolute path without '..'."
        )
    if path_style in (PathStyle.windows, PathStyle.mapped_drive):
        if not _DRIVE_LETTER.fullmatch(path.drive):
            raise Problem(
                422,
                "validation_failed",
                f"windows/mapped_drive {field_name} must start with a drive letter, e.g. Z:\\Docs.",
            )
    elif path_style is PathStyle.unc and not path.drive.startswith("\\\\"):
        raise Problem(
            422,
            "validation_failed",
            f"unc {field_name} must look like \\\\server\\share\\folder.",
        )
    allowed_roots = _allowed_roots(settings)
    if not any(path == allowed or path.is_relative_to(allowed) for allowed in allowed_roots):
        raise Problem(
            422,
            "validation_failed",
            f"{field_name} must be under an allowed source root.",
        )
    return path


def _scan_dir_entries(fs_path: Path) -> list[dict[str, str]]:
    """One level of a real directory. Never follows symlinks; tolerates
    per-entry OSError (permission denied / vanished mid-listing)."""
    entries: list[dict[str, str]] = []
    with os.scandir(fs_path) as it:
        for entry in it:
            try:
                if entry.is_symlink():
                    continue
                kind = "dir" if entry.is_dir(follow_symlinks=False) else "file"
            except OSError:
                continue
            entries.append({"name": entry.name, "kind": kind})
    entries.sort(key=lambda e: (e["kind"] != "dir", e["name"].lower()))
    return entries


def _roots_overlap(candidate: PurePath, other_raw: str, other_style: str) -> bool:
    other = _pure_path(other_raw, PathStyle(other_style))
    if isinstance(candidate, PureWindowsPath) != isinstance(other, PureWindowsPath):
        return False
    return candidate == other or candidate.is_relative_to(other) or other.is_relative_to(candidate)


async def _check_overlap(
    session: AsyncSession, scan_root: PurePath, *, exclude_id: uuid.UUID | None = None
) -> None:
    """Overlapping active roots produce confusing duplicates (TECHSTACK 5.2)."""
    stmt = select(SourceLocation).where(SourceLocation.enabled.is_(True))
    for existing in (await session.scalars(stmt)).all():
        if exclude_id is not None and existing.id == exclude_id:
            continue
        if _roots_overlap(scan_root, existing.scan_root, existing.path_style):
            raise Problem(
                409,
                "conflict",
                f"scan_root overlaps enabled location '{existing.name}'.",
            )


async def _load_location(session: AsyncSession, location_id: str) -> SourceLocation:
    location = await session.get(SourceLocation, parse_uuid(location_id, what="location"))
    if location is None:
        raise Problem(404, "not_found", "No such location.")
    return location


def _require_if_match(request: Request, location: SourceLocation) -> None:
    supplied = request.headers.get("If-Match")
    if supplied is None:
        raise Problem(428, "precondition_required", "This mutation requires an If-Match header.")
    current = location_etag(location)
    if supplied != current:
        raise Problem(
            412,
            "precondition_failed",
            "The supplied ETag is stale; refetch the resource.",
            extensions={"current_etag": current},
            headers={"ETag": current},
        )


@router.get("")
async def list_locations(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    limit = parse_limit(request)
    sort = parse_sort(request, allowed=dict.fromkeys(_SORTS, ""), default="-updated_at")
    column, descending = _SORTS[sort]
    stmt = select(SourceLocation)
    cursor = request.query_params.get("cursor")
    if cursor:
        raw_value, last_id = decode_cursor(
            settings, cursor, route="/api/v1/locations", sort=sort, filters={}
        )
        last_value: Any = raw_value
        if column.key == "updated_at":
            try:
                last_value = datetime.fromisoformat(raw_value)
            except ValueError as exc:
                raise Problem(
                    400, "invalid_cursor", "The pagination cursor is not valid here."
                ) from exc
        if descending:
            stmt = stmt.where(
                (column < last_value)
                | ((column == last_value) & (SourceLocation.id > uuid.UUID(last_id)))
            )
        else:
            stmt = stmt.where(
                (column > last_value)
                | ((column == last_value) & (SourceLocation.id > uuid.UUID(last_id)))
            )
    ordered = stmt.order_by(column.desc() if descending else column.asc(), SourceLocation.id.asc())
    rows = (await session.scalars(ordered.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        raw_value = getattr(last, column.key)
        next_cursor = encode_cursor(
            settings,
            route="/api/v1/locations",
            sort=sort,
            filters={},
            last_key=[str(raw_value), str(last.id)],
        )
    return collection_envelope(
        request,
        [serialize_location(row) for row in rows],
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
        effective_sort=[sort, "id"],
    )


@router.post("", status_code=201)
async def create_location(
    request: Request,
    response: Response,
    body: LocationCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    scan_root = _validate_scan_root(settings, body.scan_root, body.path_style)
    location = SourceLocation(
        name=body.name,
        scan_root=str(scan_root),
        display_root=body.display_root or body.scan_root,
        path_style=body.path_style.value,
        enabled=body.enabled,
        external_generation_policy=body.external_generation_policy.value,
        include_extensions=body.include_extensions,
        exclude_globs=body.exclude_globs,
        scan_interval_minutes=body.scan_interval_minutes,
    )
    await _check_overlap(session, scan_root)
    session.add(location)
    try:
        await session.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Problem(409, "conflict", "A location with this name already exists.") from exc
    response.headers["Location"] = f"/api/v1/locations/{location.id}"
    response.headers["ETag"] = location_etag(location)
    return envelope(request, serialize_location(location))


@router.get("/browse")
async def browse_locations(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    path: str | None = None,
) -> dict[str, Any]:
    """Shallow, one-level directory listing restricted to allowed source roots.

    Registered before ``/{location_id}`` so FastAPI does not match "browse" as a
    location id. Enumeration uses host-native ``pathlib``/``os`` (same as the
    scan handler). The path style is derived from the path's own shape — a
    container mount such as ``/hostfs`` is posix even when it exposes a Windows
    drive, so a client-declared style must never override that.
    """
    if path is None:
        roots = _allowed_roots(settings)
        entries = [{"name": str(r), "path": str(r), "kind": "dir"} for r in roots]
        return envelope(
            request,
            {"path": None, "path_style": None, "parent": None, "entries": entries},
        )

    style = PathStyle(detect_path_style(path))
    validated = _validate_scan_root(settings, path, style, field_name="path")
    fs_path = Path(str(validated))
    if not fs_path.is_dir():
        raise Problem(404, "not_found", "path is not a reachable directory.")
    try:
        raw_entries = _scan_dir_entries(fs_path)
    except OSError as exc:
        raise Problem(404, "not_found", "path is not a reachable directory.") from exc

    at_root = any(validated == r for r in _allowed_roots(settings))
    parent = None if at_root else str(validated.parent)
    entries = [
        {"name": e["name"], "path": str(validated / e["name"]), "kind": e["kind"]}
        for e in raw_entries
    ]
    return envelope(
        request,
        {
            "path": str(validated),
            "path_style": style.value,
            "parent": parent,
            "entries": entries,
        },
    )


@router.get("/capabilities")
async def location_capabilities(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    """Filesystem profile + whether a native OS folder dialog can be shown.

    The frontend uses this to default the path style and to decide whether the
    "Browse…" button opens the native picker or the in-app directory browser.
    """
    return envelope(
        request,
        {
            "filesystem_profile": settings.resolved_filesystem_profile,
            "native_picker_available": native_picker_available(),
        },
    )


@router.post("/pick-folder")
async def pick_folder(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    """Open the host's native folder dialog and return the chosen path.

    Only works where a native dialog is reachable (native Windows, or WSL with
    Windows interop). Unavailable in a headless container → 422 so the frontend
    falls back to the in-app browser. A cancelled dialog returns path=null.
    """
    if not native_picker_available():
        raise Problem(
            422,
            "native_picker_unavailable",
            "No native folder dialog is available here; use the in-app browser.",
        )
    path = await asyncio.to_thread(pick_folder_native)
    if path is None:
        return envelope(request, {"path": None, "path_style": None})
    return envelope(request, {"path": path, "path_style": detect_path_style(path)})


@router.get("/{location_id}", response_model=None)
async def get_location(
    request: Request,
    response: Response,
    location_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | Response:
    location = await _load_location(session, location_id)
    etag = location_etag(location)
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return envelope(request, serialize_location(location))


@router.patch("/{location_id}")
async def patch_location(
    request: Request,
    response: Response,
    location_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";")[0].strip() != (
        "application/merge-patch+json"
    ):
        raise Problem(
            415,
            "bad_request",
            "Partial updates use Content-Type: application/merge-patch+json.",
        )
    try:
        raw = await request.json()
    except ValueError as exc:
        raise Problem(400, "bad_request", "Request body is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise Problem(422, "validation_failed", "merge-patch body must be a JSON object.")
    # Explicit null clears only nullable fields (contract section 2.1).
    nullable = {"scan_interval_minutes", "display_root"}
    for key, value in raw.items():
        if value is None and key not in nullable:
            raise Problem(422, "validation_failed", f"field '{key}' is not nullable.")
    patch = LocationPatch.model_validate({k: v for k, v in raw.items() if v is not None})

    location = await _load_location(session, location_id)
    _require_if_match(request, location)
    if patch.name is not None:
        location.name = patch.name
    if "display_root" in raw:
        location.display_root = raw["display_root"] or location.scan_root
    if patch.enabled is not None:
        if patch.enabled and not location.enabled:
            await _check_overlap(
                session,
                _pure_path(location.scan_root, PathStyle(location.path_style)),
                exclude_id=location.id,
            )
        location.enabled = patch.enabled
    if patch.external_generation_policy is not None:
        location.external_generation_policy = patch.external_generation_policy.value
    if patch.include_extensions is not None:
        location.include_extensions = LocationCreate._normalize_extensions(patch.include_extensions)
    if patch.exclude_globs is not None:
        location.exclude_globs = patch.exclude_globs
    if "scan_interval_minutes" in raw:
        location.scan_interval_minutes = patch.scan_interval_minutes
    location.revision += 1
    try:
        await session.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Problem(409, "conflict", "A location with this name already exists.") from exc
    response.headers["ETag"] = location_etag(location)
    return envelope(request, serialize_location(location))


@router.delete("/{location_id}", status_code=204)
async def delete_location(
    request: Request,
    location_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Remove a location and its catalog entries (FK cascade).

    Guarded: refuse while a scan is still open so we never orphan running work.
    Requires If-Match, matching the optimistic concurrency of PATCH.
    """
    location = await _load_location(session, location_id)
    _require_if_match(request, location)
    open_jobs = await session.scalar(
        select(func.count())
        .select_from(IngestionJob)
        .where(
            IngestionJob.source_location_id == location.id,
            IngestionJob.status.in_([s.value for s in OPEN_STATUSES]),
        )
    )
    if open_jobs:
        raise Problem(
            409,
            "conflict",
            "This location has in-flight scans; cancel them before deleting.",
        )
    await session.delete(location)
    await session.commit()
    return Response(status_code=204)


@router.post("/{location_id}/test")
async def test_location(
    request: Request,
    location_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, Any]:
    """Synchronous reachability/sentinel check; mutates nothing."""
    location = await _load_location(session, location_id)
    root = Path(location.scan_root)
    checks: list[dict[str, Any]] = []
    root_ok = root.is_dir()
    checks.append(
        {
            "name": "scan_root_exists",
            "ok": root_ok,
            "detail": "" if root_ok else "scan root is not reachable",
        }
    )
    sentinel_name = settings.nas_mount_sentinel
    if location.sentinel_id is not None:
        result = check_source_sentinel(root, location.sentinel_id, sentinel_name)
        checks.append({"name": result.name, "ok": result.ok, "detail": result.detail})
    elif root_ok:
        sentinel = root / sentinel_name
        observed = sentinel.read_text(encoding="utf-8").strip() if sentinel.is_file() else None
        checks.append(
            {
                "name": "source_sentinel",
                "ok": True,
                "detail": (
                    "sentinel present; it will be adopted by the first successful scan"
                    if observed
                    else "no sentinel file; advisory for non-mapped roots"
                ),
            }
        )
    return envelope(request, {"ok": all(c["ok"] for c in checks), "checks": checks})


@router.post("/{location_id}/scan", status_code=202)
async def request_scan(
    request: Request,
    response: Response,
    location_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
) -> dict[str, Any]:
    engine = JobEngine(
        base_delay_seconds=settings.job_retry_base_delay_seconds,
        max_delay_seconds=settings.job_retry_max_delay_seconds,
    )
    fingerprint = request_fingerprint({"location_id": location_id}, None)
    location = await _load_location(session, location_id)
    outcome = await reserve_idempotency(
        session,
        method="POST",
        route_template="/api/v1/locations/{location_id}/scan",
        key=idempotency_key,
        fingerprint=fingerprint,
    )
    if outcome.replayed_job is not None:
        job, coalesced, replayed = outcome.replayed_job, False, True
    else:
        job, coalesced = await engine.enqueue(
            session,
            job_type=JobType.scan_location,
            payload={"version": 1, "source_location_id": str(location.id)},
            origin=JobOrigin.api,
            source_location_id=location.id,
            max_attempts=settings.job_max_attempts,
            request_key=idempotency_key,
            actor="api",
        )
        replayed = False
        assert outcome.record is not None
        outcome.record.job_id = job.id
    await session.commit()
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    response.headers["Retry-After"] = "2"
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return envelope(
        request,
        serialize_job(job),
        idempotency_replayed=replayed,
        coalesced=coalesced,
    )
