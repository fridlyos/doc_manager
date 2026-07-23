"""Generation provider errors (TECHSTACK 5.13, contract §8).

Transport/provider faults raise ``GenerationError`` with a stable code that maps
to a Problem/HTTP status and to the SSE ``ask.error`` event. These are distinct
from *semantic* outcomes carried as events — a refusal (`GenRefusal`) and
insufficient evidence are 200 results, not errors. Policy denial and unknown
providers live here because they are request-level failures.
"""

from __future__ import annotations

from enum import StrEnum


class GenerationErrorCode(StrEnum):
    unknown_provider = "unknown_provider"
    provider_unavailable = "provider_unavailable"
    provider_timeout = "provider_timeout"
    provider_authentication_failed = "provider_authentication_failed"
    provider_rate_limited = "provider_rate_limited"
    provider_error = "provider_error"
    #: A request would transfer evidence to an external provider that a deployment
    #: or an evidence-bearing source location forbids. Fails closed (§12).
    external_policy_denied = "external_policy_denied"


#: Stable code → HTTP status (contract §4). Retryability is per-instance.
_HTTP_STATUS = {
    GenerationErrorCode.unknown_provider: 404,
    GenerationErrorCode.provider_unavailable: 503,
    GenerationErrorCode.provider_timeout: 504,
    GenerationErrorCode.provider_authentication_failed: 401,
    GenerationErrorCode.provider_rate_limited: 429,
    GenerationErrorCode.provider_error: 502,
    GenerationErrorCode.external_policy_denied: 403,
}

#: Codes whose default posture is retryable (a later attempt may succeed).
_RETRYABLE = {
    GenerationErrorCode.provider_unavailable,
    GenerationErrorCode.provider_timeout,
    GenerationErrorCode.provider_rate_limited,
}


class GenerationError(Exception):
    """A provider/transport fault surfaced to the Ask layer."""

    def __init__(
        self, code: GenerationErrorCode, message: str, *, retryable: bool | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = code in _RETRYABLE if retryable is None else retryable

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self.code]
