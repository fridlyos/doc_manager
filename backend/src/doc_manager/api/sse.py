"""Server-Sent Events framing for Ask streaming (contract §8.3).

Hand-rolled rather than a library so the exact wire contract holds: an
incrementing decimal ``id``, a provider-neutral ``event`` name, a single-line
JSON ``data`` object carrying the common fields, and ``: keep-alive`` comments
(which are not events and carry no id).
"""

from __future__ import annotations

import json
from typing import Any

STREAM_VERSION = "1.0"

SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "X-Content-Type-Options": "nosniff",
}


def sse_event(*, event_id: int, event: str, data: dict[str, Any]) -> str:
    """One SSE frame. ``data`` is serialized as a single compact JSON line."""
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


def sse_comment(text: str) -> str:
    """A comment line (e.g. keep-alive). Not an event; has no id or sequence."""
    return f": {text}\n\n"


def common_fields(
    *, sequence: int, request_id: str, ask_id: str, occurred_at: str
) -> dict[str, Any]:
    """Fields present on every data event (contract §8.3)."""
    return {
        "stream_version": STREAM_VERSION,
        "sequence": sequence,
        "request_id": request_id,
        "ask_id": ask_id,
        "occurred_at": occurred_at,
    }
