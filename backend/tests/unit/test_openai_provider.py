"""OpenAI Responses adapter: event normalization, error mapping, safe payload.

Offline — a fake async client stands in for the SDK; error mapping uses real
``openai`` exception instances. No network, no key needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from doc_manager.core.config import Settings
from doc_manager.generation import (
    DataBoundary,
    FinishReason,
    GenDelta,
    GenerationError,
    GenFinished,
    GenRefusal,
    GenStarted,
    GenUsage,
)
from doc_manager.generation.base import GenerationRequest
from doc_manager.generation.errors import GenerationErrorCode
from doc_manager.generation.openai_provider import OpenAIProvider

# The OpenAI adapter is behind the optional `openai` extra; skip the whole module
# (including the SDK-error parametrization below) when it is not installed.
openai = pytest.importorskip("openai")


def _usage(i: int = 40, o: int = 8) -> SimpleNamespace:
    return SimpleNamespace(input_tokens=i, output_tokens=o, total_tokens=i + o)


def _delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _completed() -> SimpleNamespace:
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=_usage()))


class _FakeResponses:
    def __init__(self, events: list[Any], exc: Exception | None) -> None:
        self._events = events
        self._exc = exc
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.kwargs = kwargs
        if self._exc is not None:
            raise self._exc

        async def gen() -> AsyncIterator[Any]:
            for event in self._events:
                yield event

        return gen()


class _FakeModels:
    def __init__(self, exc: Exception | None) -> None:
        self._exc = exc

    async def retrieve(self, model: str) -> SimpleNamespace:
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(id=model)


class FakeClient:
    def __init__(
        self,
        events: list[Any] | None = None,
        *,
        create_exc: Exception | None = None,
        retrieve_exc: Exception | None = None,
    ) -> None:
        self.responses = _FakeResponses(events or [], create_exc)
        self.models = _FakeModels(retrieve_exc)


def _provider(client: FakeClient, *, api_key: str | None = "sk-test") -> OpenAIProvider:
    return OpenAIProvider(
        model="gpt-test",
        api_key=api_key,
        max_output_tokens=100,
        context_tokens=128_000,
        timeout_seconds=90.0,
        client=cast("Any", client),
    )


def _request() -> GenerationRequest:
    return GenerationRequest(
        system_prompt="Answer only from evidence [E1].",
        user_prompt="When does it renew?",
        max_output_tokens=100,
    )


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/responses")


def test_identity_and_secret() -> None:
    provider = _provider(FakeClient())
    assert provider.provider_id == "openai"
    assert provider.data_boundary is DataBoundary.external
    assert provider.secret_available(Settings()) is True
    assert _provider(FakeClient(), api_key=None).secret_available(Settings()) is False


async def test_generate_normalizes_and_sends_stateless_payload() -> None:
    client = FakeClient([_delta("It renews "), _delta("in December."), _completed()])
    provider = _provider(client)
    events = [ev async for ev in provider.generate(_request())]

    assert isinstance(events[0], GenStarted) and events[0].data_boundary == "external"
    assert [e.text for e in events if isinstance(e, GenDelta)] == ["It renews ", "in December."]
    assert any(isinstance(e, GenUsage) and e.usage.total_tokens == 48 for e in events)
    assert isinstance(events[-1], GenFinished) and events[-1].reason is FinishReason.stop

    # Stateless + no hosted tools/files; only instructions + input text sent.
    kw = client.responses.kwargs
    assert kw["store"] is False and kw["stream"] is True
    assert kw["instructions"] == "Answer only from evidence [E1]."
    assert kw["input"] == "When does it renew?"
    for forbidden in ("tools", "previous_response_id", "conversation", "background"):
        assert forbidden not in kw


async def test_refusal_becomes_gen_refusal() -> None:
    refusal = SimpleNamespace(type="response.refusal.delta", delta="I can't help with that.")
    client = FakeClient([refusal, _completed()])
    events = [ev async for ev in _provider(client).generate(_request())]
    assert isinstance(events[-1], GenRefusal)
    assert events[-1].message == "I can't help with that."


async def test_incomplete_maps_to_length() -> None:
    incomplete = SimpleNamespace(
        type="response.incomplete", response=SimpleNamespace(usage=_usage())
    )
    client = FakeClient([_delta("partial"), incomplete])
    events = [ev async for ev in _provider(client).generate(_request())]
    assert isinstance(events[-1], GenFinished) and events[-1].reason is FinishReason.length


async def test_stream_failure_is_provider_error() -> None:
    failed = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(error=SimpleNamespace(message="model exploded")),
    )
    client = FakeClient([_delta("x"), failed])
    with pytest.raises(GenerationError) as exc:
        async for _ in _provider(client).generate(_request()):
            pass
    assert exc.value.code is GenerationErrorCode.provider_error


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (
            openai.AuthenticationError(
                "bad key", response=httpx.Response(401, request=_req()), body=None
            ),
            GenerationErrorCode.provider_authentication_failed,
        ),
        (
            openai.RateLimitError(
                "slow down", response=httpx.Response(429, request=_req()), body=None
            ),
            GenerationErrorCode.provider_rate_limited,
        ),
        (openai.APITimeoutError(request=_req()), GenerationErrorCode.provider_timeout),
        (
            openai.APIConnectionError(message="down", request=_req()),
            GenerationErrorCode.provider_unavailable,
        ),
    ],
)
async def test_sdk_errors_are_mapped(exc: Exception, code: GenerationErrorCode) -> None:
    provider = _provider(FakeClient(create_exc=exc))
    with pytest.raises(GenerationError) as raised:
        async for _ in provider.generate(_request()):
            pass
    assert raised.value.code is code


async def test_readiness_ok_and_no_key() -> None:
    ok = await _provider(FakeClient()).readiness()
    assert ok.ready is True and ok.model_id == "gpt-test"

    no_key = await _provider(FakeClient(), api_key=None).readiness()
    assert no_key.ready is False and "no API key" in no_key.detail


async def test_readiness_auth_failure_is_not_ready() -> None:
    exc = openai.AuthenticationError(
        "bad key", response=httpx.Response(401, request=_req()), body=None
    )
    ready = await _provider(FakeClient(retrieve_exc=exc)).readiness()
    assert ready.ready is False
    assert ready.detail == "provider_authentication_failed"


def test_build_registry_includes_openai_when_configured() -> None:
    from doc_manager.generation import build_registry

    settings = Settings(
        external_llm_enabled=True,
        openai_model="gpt-test",
        external_provider_allowlist="openai",
    )
    reg = build_registry(settings)
    assert "openai" in [p.provider_id for p in reg.all()]
    # Registered, but eligibility still needs a secret (none here) -> ineligible.
    assert reg.eligible_ids(settings) == ["ollama"]


def test_build_registry_omits_openai_without_model() -> None:
    from doc_manager.generation import build_registry

    reg = build_registry(Settings(openai_model=None))
    assert [p.provider_id for p in reg.all()] == ["ollama"]
