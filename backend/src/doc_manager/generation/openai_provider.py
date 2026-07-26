"""OpenAI Responses adapter — opt-in external provider (TECHSTACK 5.13).

Uses the official SDK's **Responses API** with ``store=false`` and stateless
streaming: no ``previous_response_id``, Conversations, background mode, hosted
file search, web search, tools, or file uploads. The key is read from a
Docker-secret / env injection available only to the API service — never from
PostgreSQL, the browser, logs, or Problem details.

The ``openai`` package is an optional extra; it is imported lazily so the base
install (and ``import doc_manager.generation``) works without it. The adapter is
only *registered* when the package is importable (see ``registry.build_registry``)
and only *usable* after the deployment opt-in + allowlist + secret pass the
registry gate and the external-processing policy (Phase 5.c) allows the transfer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from doc_manager.core.config import Settings
from doc_manager.core.logging import get_logger
from doc_manager.generation.base import (
    DataBoundary,
    GenerationRequest,
    ProviderCapabilities,
    ProviderReadiness,
)
from doc_manager.generation.errors import GenerationError, GenerationErrorCode
from doc_manager.generation.events import (
    FinishReason,
    GenDelta,
    GenerationEvent,
    GenFinished,
    GenRefusal,
    GenStarted,
    GenUsage,
    Usage,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI

log = get_logger("doc_manager.generation.openai")


class OpenAIProvider:
    provider_id = "openai"
    data_boundary = DataBoundary.external

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        max_output_tokens: int,
        context_tokens: int,
        timeout_seconds: float,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self.capabilities = ProviderCapabilities(
            context_tokens=context_tokens, max_output_tokens=max_output_tokens
        )
        # Injected in tests; otherwise built lazily from the secret.
        self._client = client

    def secret_available(self, settings: Settings) -> bool:
        return bool(self._api_key)

    def _make_client(self) -> AsyncOpenAI:
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=self._api_key, timeout=self._timeout, max_retries=0)

    async def readiness(self) -> ProviderReadiness:
        if not self._api_key:
            return ProviderReadiness(
                ready=False, detail="no API key configured", model_id=self._model
            )
        client = self._client or self._make_client()
        try:
            await client.models.retrieve(self._model)
        except Exception as exc:  # noqa: BLE001 - map any SDK error to not-ready
            mapped = _map_openai_error(exc)
            detail = mapped.code.value if mapped else type(exc).__name__
            return ProviderReadiness(ready=False, detail=detail, model_id=self._model)
        return ProviderReadiness(ready=True, model_id=self._model)

    async def generate(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        client = self._client or self._make_client()
        model = request.model_id or self._model
        try:
            stream = await client.responses.create(
                model=model,
                instructions=request.system_prompt,
                input=request.user_prompt,
                max_output_tokens=request.max_output_tokens,
                store=False,  # stateless; nothing retained server-side.
                stream=True,
            )
            yield GenStarted(self.provider_id, model, self.data_boundary.value)
            async for event in _normalize(stream):
                yield event
        except GenerationError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize SDK errors
            mapped = _map_openai_error(exc)
            if mapped is not None:
                raise mapped from exc
            raise GenerationError(
                GenerationErrorCode.provider_error, f"openai request failed: {type(exc).__name__}"
            ) from exc


async def _normalize(stream: AsyncIterator[Any]) -> AsyncIterator[GenerationEvent]:
    refusal = ""
    async for event in stream:
        etype = getattr(event, "type", "")
        if etype == "response.output_text.delta":
            if event.delta:
                yield GenDelta(event.delta)
        elif etype == "response.refusal.delta":
            refusal += event.delta or ""
        elif etype == "response.incomplete":
            usage = _usage(getattr(event, "response", None))
            yield GenUsage(usage)
            yield GenFinished(FinishReason.length, usage)
            return
        elif etype == "response.completed":
            usage = _usage(getattr(event, "response", None))
            if refusal:
                yield GenRefusal(refusal)
                return
            yield GenUsage(usage)
            yield GenFinished(FinishReason.stop, usage)
            return
        elif etype in ("response.failed", "error"):
            raise _map_stream_failure(event)


def _usage(response: Any) -> Usage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return Usage()
    return Usage(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )


def _map_stream_failure(event: Any) -> GenerationError:
    err = getattr(getattr(event, "response", None), "error", None) or event
    message = getattr(err, "message", None) or "openai generation failed"
    return GenerationError(GenerationErrorCode.provider_error, str(message))


def _map_openai_error(exc: BaseException) -> GenerationError | None:
    """Map an ``openai`` SDK exception to a normalized ``GenerationError``.

    Returns ``None`` when ``exc`` is not an OpenAI error (or the SDK is absent).
    """
    try:
        import openai
    except ImportError:  # pragma: no cover - SDK present whenever this runs
        return None
    if isinstance(exc, openai.AuthenticationError):
        return GenerationError(
            GenerationErrorCode.provider_authentication_failed, "openai authentication failed"
        )
    if isinstance(exc, openai.RateLimitError):
        return GenerationError(GenerationErrorCode.provider_rate_limited, "openai rate limited")
    if isinstance(exc, openai.APITimeoutError):
        return GenerationError(GenerationErrorCode.provider_timeout, "openai request timed out")
    if isinstance(exc, openai.APIConnectionError):
        return GenerationError(
            GenerationErrorCode.provider_unavailable, "openai endpoint is unreachable"
        )
    if isinstance(exc, openai.APIError):
        return GenerationError(GenerationErrorCode.provider_error, "openai API error")
    return None


def build_openai_provider(
    settings: Settings, *, client: AsyncOpenAI | None = None
) -> OpenAIProvider:
    """Build the adapter from settings. ``openai_model`` must be configured."""
    if not settings.openai_model:
        raise ValueError("openai_model must be configured to build the OpenAI provider")
    return OpenAIProvider(
        model=settings.openai_model,
        api_key=settings.read_openai_api_key(),
        max_output_tokens=settings.external_max_output_tokens,
        context_tokens=settings.openai_context_tokens,
        timeout_seconds=float(settings.external_request_timeout_seconds),
        client=client,
    )
