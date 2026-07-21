"""Success envelopes and canonical timestamp serialization (contract secs. 2-3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from doc_manager.api.errors import API_VERSION, request_id_of


def iso_utc(value: datetime | None) -> str | None:
    """RFC 3339 UTC with literal Z; fractional seconds trimmed of zeros."""
    if value is None:
        return None
    stamp = value.astimezone(UTC).isoformat(timespec="microseconds")
    stamp = stamp.removesuffix("+00:00")
    if "." in stamp:
        stamp = stamp.rstrip("0").rstrip(".")
    return stamp + "Z"


def envelope(request: Request, data: Any, **meta: Any) -> dict[str, Any]:
    return {
        "data": data,
        "meta": {
            "api_version": API_VERSION,
            "request_id": request_id_of(request),
            **meta,
        },
    }


def collection_envelope(
    request: Request,
    data: list[Any],
    *,
    limit: int,
    has_more: bool,
    next_cursor: str | None,
    effective_sort: list[str],
    **meta: Any,
) -> dict[str, Any]:
    return {
        "data": data,
        "page": {"limit": limit, "has_more": has_more, "next_cursor": next_cursor},
        "meta": {
            "api_version": API_VERSION,
            "request_id": request_id_of(request),
            "effective_sort": effective_sort,
            **meta,
        },
    }
