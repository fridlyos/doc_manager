"""Problem Details errors and the request-correlation middleware.

Every error is `application/problem+json` with a stable `code` clients branch
on (API contract section 4). Problems never expose stack traces, SQL, paths,
or secrets.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

API_VERSION = "1"
_UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

_TITLES = {
    "bad_request": "Malformed request",
    "invalid_request_id": "Invalid X-Request-ID header",
    "invalid_cursor": "Invalid pagination cursor",
    "cursor_expired": "Pagination cursor expired",
    "idempotency_key_required": "Idempotency-Key header required",
    "validation_failed": "Request validation failed",
    "not_found": "Resource not found",
    "conflict": "Conflicting resource state",
    "idempotency_conflict": "Idempotency key reused with a different request",
    "idempotency_in_progress": "Request with this idempotency key is in progress",
    "job_not_cancellable": "Job is not cancellable",
    "job_not_retryable": "Job is not retryable",
    "precondition_required": "If-Match header required",
    "precondition_failed": "Stale If-Match value",
    "source_unavailable": "Source mount unavailable",
    "native_picker_unavailable": "Native folder picker unavailable",
    "dependency_unavailable": "Required local service unavailable",
    "internal_error": "Internal server error",
}


class Problem(Exception):
    """Raise from routes; the handler renders Problem Details."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        retryable: bool = False,
        errors: list[dict[str, str]] | None = None,
        extensions: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.errors = errors
        self.extensions = extensions or {}
        self.headers = headers or {}


def request_id_of(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    return rid if isinstance(rid, str) else str(uuid.uuid4())


def problem_body(request: Request, exc: Problem) -> dict[str, Any]:
    """The Problem Details JSON object — shared by HTTP responses and SSE errors."""
    rid = request_id_of(request)
    body: dict[str, Any] = {
        "type": f"urn:doc-manager:problem:{exc.code}",
        "title": _TITLES.get(exc.code, exc.code),
        "status": exc.status,
        "detail": exc.detail,
        "instance": f"urn:doc-manager:request:{rid}",
        "code": exc.code,
        "request_id": rid,
        "retryable": exc.retryable,
    }
    if exc.errors:
        body["errors"] = exc.errors
    body.update(exc.extensions)
    return body


def problem_response(request: Request, exc: Problem) -> JSONResponse:
    rid = request_id_of(request)
    body = problem_body(request, exc)
    return JSONResponse(
        body,
        status_code=exc.status,
        media_type="application/problem+json; charset=utf-8",
        headers={
            "X-Request-ID": rid,
            "Docman-Api-Version": API_VERSION,
            **exc.headers,
        },
    )


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Canonical UUIDv4 request correlation (contract section 1.3)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("X-Request-ID")
        if supplied is not None and not _UUID4_RE.match(supplied):
            # The rejection itself gets a fresh server-generated request ID.
            request.state.request_id = str(uuid.uuid4())
            return problem_response(
                request,
                Problem(
                    400,
                    "invalid_request_id",
                    "X-Request-ID must be a canonical lowercase UUIDv4.",
                ),
            )
        request.state.request_id = supplied or str(uuid.uuid4())
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request.state.request_id)
        response.headers.setdefault("Docman-Api-Version", API_VERSION)
        return response


async def _problem_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, Problem)
    return problem_response(request, exc)


async def _validation_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part != "body"]
        errors.append(
            {
                "pointer": "/" + "/".join(loc) if loc else "/",
                "code": str(err.get("type", "invalid")),
                "message": str(err.get("msg", "Invalid value.")),
            }
        )
    return problem_response(
        request,
        Problem(
            422,
            "validation_failed",
            "One or more request fields are invalid.",
            errors=errors,
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Problem, _problem_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
