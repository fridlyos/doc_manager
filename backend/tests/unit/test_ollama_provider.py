"""Ollama adapter: NDJSON stream normalization, readiness, error mapping.

Offline via httpx.MockTransport — no live Ollama. Asserts the local provider
emits normalized events, parses usage/finish, and maps transport faults.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from doc_manager.core.config import Settings
from doc_manager.generation import (
    DataBoundary,
    FinishReason,
    GenDelta,
    GenerationError,
    GenFinished,
    GenStarted,
    GenUsage,
    build_registry,
)
from doc_manager.generation.base import GenerationRequest
from doc_manager.generation.errors import GenerationErrorCode
from doc_manager.generation.ollama import OllamaProvider, build_ollama_provider


def _transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _provider(
    handler: Callable[[httpx.Request], httpx.Response], model: str = "llama3.1:8b"
) -> OllamaProvider:
    settings = Settings(ollama_chat_model=model)
    return build_ollama_provider(settings, transport=_transport(handler))


def _request() -> GenerationRequest:
    return GenerationRequest(
        system_prompt="ground rules", user_prompt="when does it renew?", max_output_tokens=64
    )


def _ndjson(*objs: dict[str, object]) -> str:
    return "\n".join(json.dumps(o) for o in objs) + "\n"


def test_provider_identity() -> None:
    provider = _provider(lambda r: httpx.Response(200))
    assert provider.provider_id == "ollama"
    assert provider.data_boundary is DataBoundary.local
    assert provider.secret_available(Settings()) is True
    assert provider.capabilities.context_tokens == 8192


async def test_generate_normalizes_stream() -> None:
    body = _ndjson(
        {"message": {"role": "assistant", "content": "It renews "}, "done": False},
        {"message": {"role": "assistant", "content": "in December."}, "done": False},
        {
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 40,
            "eval_count": 8,
        },
    )
    provider = _provider(lambda r: httpx.Response(200, text=body))
    events = [ev async for ev in provider.generate(_request())]

    assert isinstance(events[0], GenStarted)
    assert events[0].provider_id == "ollama" and events[0].data_boundary == "local"
    deltas = [e.text for e in events if isinstance(e, GenDelta)]
    assert deltas == ["It renews ", "in December."]
    usage = next(e for e in events if isinstance(e, GenUsage))
    assert usage.usage.input_tokens == 40 and usage.usage.output_tokens == 8
    assert usage.usage.total_tokens == 48
    finished = events[-1]
    assert isinstance(finished, GenFinished) and finished.reason is FinishReason.stop


async def test_generate_maps_length_finish_reason() -> None:
    body = _ndjson(
        {"message": {"content": "partial"}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "length"},
    )
    provider = _provider(lambda r: httpx.Response(200, text=body))
    events = [ev async for ev in provider.generate(_request())]
    assert isinstance(events[-1], GenFinished) and events[-1].reason is FinishReason.length


async def test_generate_unreachable_endpoint_is_provider_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = _provider(boom)
    with pytest.raises(GenerationError) as exc:
        async for _ in provider.generate(_request()):
            pass
    assert exc.value.code is GenerationErrorCode.provider_unavailable


async def test_generate_http_error_is_provider_error() -> None:
    provider = _provider(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(GenerationError) as exc:
        async for _ in provider.generate(_request()):
            pass
    assert exc.value.code is GenerationErrorCode.provider_error


async def test_generate_inline_error_is_provider_error() -> None:
    body = _ndjson({"error": "model not found"})
    provider = _provider(lambda r: httpx.Response(200, text=body))
    with pytest.raises(GenerationError) as exc:
        async for _ in provider.generate(_request()):
            pass
    assert exc.value.code is GenerationErrorCode.provider_error


async def test_readiness_reports_model_presence() -> None:
    def tags(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})

    ready = await _provider(tags).readiness()
    assert ready.ready is True

    def other(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "mistral:7b"}]})

    not_ready = await _provider(other).readiness()
    assert not_ready.ready is False
    assert "not pulled" in not_ready.detail


async def test_readiness_unreachable_is_not_ready() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    ready = await _provider(boom).readiness()
    assert ready.ready is False


def test_build_registry_includes_ollama() -> None:
    reg = build_registry(Settings())
    assert "ollama" in [p.provider_id for p in reg.all()]
    # Local provider is eligible even with external generation disabled.
    assert reg.eligible_ids(Settings(external_llm_enabled=False)) == ["ollama"]
