"""Phase 5.a generation foundation: events, errors, timeout, registry gating.

Pure/offline — no providers, no network. Fake adapters exercise the registry's
eligibility gate and the timeout wrapper.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from doc_manager.core.config import Settings
from doc_manager.generation import (
    DataBoundary,
    FinishReason,
    GenDelta,
    GenerationError,
    GenerationEvent,
    GenFinished,
    GenStarted,
    ProviderCapabilities,
    ProviderReadiness,
    ProviderRegistry,
    Usage,
    build_registry,
    stream_with_timeout,
)
from doc_manager.generation.errors import GenerationErrorCode


class FakeProvider:
    def __init__(self, provider_id: str, boundary: DataBoundary, *, has_secret: bool = True):
        self.provider_id = provider_id
        self.data_boundary = boundary
        self.capabilities = ProviderCapabilities(context_tokens=8192, max_output_tokens=1024)
        self._has_secret = has_secret

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=True, model_id="m")

    def secret_available(self, settings: Settings) -> bool:
        return self._has_secret

    async def generate(self, request: object) -> AsyncIterator[GenerationEvent]:
        yield GenStarted(self.provider_id, "m", self.data_boundary.value)
        yield GenDelta("hi")
        yield GenFinished(FinishReason.stop, usage=Usage(1, 1, 2))


# --- events -----------------------------------------------------------------


def test_event_shapes() -> None:
    assert GenDelta("x").text == "x"
    assert GenFinished(FinishReason.length).reason is FinishReason.length
    assert Usage(1, 2, 3).total_tokens == 3
    assert FinishReason.refusal.value == "refusal"


# --- errors -----------------------------------------------------------------


def test_error_http_status_and_retryability() -> None:
    assert GenerationError(GenerationErrorCode.provider_timeout, "x").http_status == 504
    assert GenerationError(GenerationErrorCode.provider_timeout, "x").retryable is True
    assert (
        GenerationError(GenerationErrorCode.provider_authentication_failed, "x").http_status == 401
    )
    assert (
        GenerationError(GenerationErrorCode.provider_authentication_failed, "x").retryable is False
    )
    assert GenerationError(GenerationErrorCode.unknown_provider, "x").http_status == 404
    # Explicit override wins.
    assert (
        GenerationError(GenerationErrorCode.provider_error, "x", retryable=True).retryable is True
    )


# --- timeout ----------------------------------------------------------------


async def _slow() -> AsyncIterator[GenerationEvent]:
    yield GenStarted("p", "m", "local")
    await asyncio.sleep(1.0)
    yield GenDelta("late")


async def _fast() -> AsyncIterator[GenerationEvent]:
    yield GenStarted("p", "m", "local")
    yield GenDelta("a")
    yield GenFinished(FinishReason.stop)


async def test_timeout_maps_to_provider_timeout() -> None:
    with pytest.raises(GenerationError) as exc:
        async for _ in stream_with_timeout(_slow(), timeout_seconds=0.05):
            pass
    assert exc.value.code is GenerationErrorCode.provider_timeout
    assert exc.value.http_status == 504


async def test_timeout_passes_through_fast_stream() -> None:
    events = [ev async for ev in stream_with_timeout(_fast(), timeout_seconds=5.0)]
    assert [type(e).__name__ for e in events] == ["GenStarted", "GenDelta", "GenFinished"]


# --- registry ---------------------------------------------------------------


def _settings(**kw: object) -> Settings:
    return Settings(**kw)  # type: ignore[arg-type]


def test_unknown_provider_raises() -> None:
    reg = ProviderRegistry([FakeProvider("ollama", DataBoundary.local)])
    with pytest.raises(GenerationError) as exc:
        reg.get("nope")
    assert exc.value.code is GenerationErrorCode.unknown_provider


def test_local_provider_always_eligible() -> None:
    reg = ProviderRegistry([FakeProvider("ollama", DataBoundary.local)])
    settings = _settings(external_llm_enabled=False)
    assert reg.eligible_ids(settings) == ["ollama"]
    assert reg.require_eligible(settings, "ollama").provider_id == "ollama"


def test_external_requires_flag_allowlist_and_secret() -> None:
    ext = FakeProvider("openai", DataBoundary.external)
    reg = ProviderRegistry([ext])

    # Flag off -> ineligible.
    off = _settings(external_llm_enabled=False, external_provider_allowlist="openai")
    assert reg.eligible_ids(off) == []
    with pytest.raises(GenerationError) as exc:
        reg.require_eligible(off, "openai")
    assert exc.value.code is GenerationErrorCode.provider_unavailable

    # Not on allowlist -> ineligible.
    not_listed = _settings(external_llm_enabled=True, external_provider_allowlist="other")
    assert reg.eligible_ids(not_listed) == []

    # Secret missing -> ineligible.
    no_secret_reg = ProviderRegistry(
        [FakeProvider("openai", DataBoundary.external, has_secret=False)]
    )
    on = _settings(external_llm_enabled=True, external_provider_allowlist="openai")
    assert no_secret_reg.eligible_ids(on) == []

    # All satisfied -> eligible.
    assert reg.eligible_ids(on) == ["openai"]
    assert reg.require_eligible(on, "openai").provider_id == "openai"


def test_no_fallback_selection_is_explicit() -> None:
    # An eligible local + an ineligible external: selecting the external never
    # silently returns the local one.
    reg = ProviderRegistry(
        [FakeProvider("ollama", DataBoundary.local), FakeProvider("openai", DataBoundary.external)]
    )
    settings = _settings(external_llm_enabled=False)
    with pytest.raises(GenerationError) as exc:
        reg.require_eligible(settings, "openai")
    assert exc.value.code is GenerationErrorCode.provider_unavailable


def test_duplicate_provider_id_rejected() -> None:
    with pytest.raises(ValueError):
        ProviderRegistry(
            [FakeProvider("dup", DataBoundary.local), FakeProvider("dup", DataBoundary.local)]
        )


def test_build_registry_registers_local_ollama() -> None:
    # The local Ollama adapter (5.b) is always registered; eligibility still gates
    # use. The external OpenAI adapter appends in 5.d.
    reg = build_registry(_settings())
    assert [p.provider_id for p in reg.all()] == ["ollama"]
