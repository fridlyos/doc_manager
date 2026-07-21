"""Opaque, integrity-protected keyset cursors (contract section 5.1).

The cursor binds route, normalized filters, effective sort, and the last key
values. The HMAC key is derived from deployment configuration, so a cursor is
useless outside its deployment and cannot be constructed by clients.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Request

from doc_manager.api.errors import Problem
from doc_manager.core.config import Settings

CURSOR_TTL_SECONDS = 15 * 60


def _key(settings: Settings) -> bytes:
    return hashlib.sha256(f"cursor:v1:{settings.database_url}".encode()).digest()


def encode_cursor(
    settings: Settings,
    *,
    route: str,
    sort: str,
    filters: dict[str, Any],
    last_key: list[str],
) -> str:
    payload = {
        "r": route,
        "s": sort,
        "f": filters,
        "k": last_key,
        "t": int(time.time()),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(_key(settings), raw, hashlib.sha256).hexdigest()[:32]
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + mac


def decode_cursor(
    settings: Settings,
    token: str,
    *,
    route: str,
    sort: str,
    filters: dict[str, Any],
) -> list[str]:
    """Validate and return the keyset values. Raises contract problems."""
    invalid = Problem(400, "invalid_cursor", "The pagination cursor is not valid here.")
    try:
        encoded, mac = token.rsplit(".", 1)
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, TypeError) as exc:
        raise invalid from exc
    if not hmac.compare_digest(hmac.new(_key(settings), raw, hashlib.sha256).hexdigest()[:32], mac):
        raise invalid
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise invalid from exc
    if not isinstance(payload, dict):
        raise invalid
    if payload.get("r") != route or payload.get("s") != sort or payload.get("f") != filters:
        raise invalid
    issued_at = payload.get("t")
    if not isinstance(issued_at, int | float):
        raise invalid
    if time.time() - issued_at > CURSOR_TTL_SECONDS:
        raise Problem(
            400,
            "cursor_expired",
            "The pagination cursor has expired; restart pagination.",
            retryable=True,
        )
    key = payload.get("k")
    if not isinstance(key, list) or len(key) != 2:
        raise invalid
    return [str(part) for part in key]


def parse_limit(request: Request) -> int:
    raw = request.query_params.get("limit", "50")
    try:
        limit = int(raw)
    except ValueError as exc:
        raise Problem(
            422, "validation_failed", "limit must be an integer between 1 and 100."
        ) from exc
    if not 1 <= limit <= 100:
        raise Problem(422, "validation_failed", "limit must be between 1 and 100.")
    return limit


def parse_sort(request: Request, *, allowed: dict[str, str], default: str) -> str:
    """Single-field sort allowlist; `id` is always the tie-breaker."""
    sort = request.query_params.get("sort", default)
    if sort not in allowed:
        raise Problem(
            422,
            "validation_failed",
            f"sort must be one of: {', '.join(sorted(allowed))}.",
        )
    return sort


def parse_bracket_filters(
    request: Request, *, allowed: dict[str, set[str] | None]
) -> dict[str, list[str]]:
    """Parse repeated `filter[field]` query parameters (contract section 5.3)."""
    filters: dict[str, list[str]] = {}
    for raw_key, value in request.query_params.multi_items():
        if not raw_key.startswith("filter[") or not raw_key.endswith("]"):
            continue
        field = raw_key[len("filter[") : -1]
        if field not in allowed:
            raise Problem(422, "validation_failed", f"unknown filter field: {field}.")
        if value == "":
            raise Problem(422, "validation_failed", f"filter[{field}] must not be empty.")
        allowed_values = allowed[field]
        if allowed_values is not None and value not in allowed_values:
            raise Problem(422, "validation_failed", f"invalid value for filter[{field}].")
        filters.setdefault(field, []).append(value)
    return filters
