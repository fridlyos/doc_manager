"""Ollama adapter — the default local generation provider (TECHSTACK 5.13).

Streams from the native Windows Ollama endpoint over its local HTTP API. All
prompt and answer content stays on the local host (``data_boundary = local``); no
external transfer, no secret. Ollama's NDJSON chat stream is normalized into the
provider-neutral event set so the Ask/RAG layer never sees Ollama's wire format.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

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
    GenStarted,
    GenUsage,
    Usage,
)

log = get_logger("doc_manager.generation.ollama")

_CONNECT_TIMEOUT = 5.0
_FINISH_REASONS = {"stop": FinishReason.stop, "length": FinishReason.length}


class OllamaProvider:
    provider_id = "ollama"
    data_boundary = DataBoundary.local

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        num_ctx: int,
        max_output_tokens: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._num_ctx = num_ctx
        self.capabilities = ProviderCapabilities(
            context_tokens=num_ctx, max_output_tokens=max_output_tokens
        )
        # Injected in tests (httpx.MockTransport); None uses a real connection.
        self._transport = transport

    def secret_available(self, settings: Settings) -> bool:
        return True  # Local provider needs no credential.

    def _client(self, *, streaming: bool) -> httpx.AsyncClient:
        # Streaming reads have no read deadline (the Ask layer bounds the whole
        # stream); a short connect timeout surfaces an unavailable endpoint fast.
        timeout = httpx.Timeout(_CONNECT_TIMEOUT, read=None if streaming else 10.0)
        return httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout, transport=self._transport
        )

    async def readiness(self) -> ProviderReadiness:
        """Confirm the endpoint answers and the configured model is pulled."""
        try:
            async with self._client(streaming=False) as client:
                resp = await client.get("/api/tags")
                resp.raise_for_status()
                names = {m.get("name", "") for m in resp.json().get("models", [])}
        except httpx.HTTPError as exc:
            return ProviderReadiness(ready=False, detail=type(exc).__name__, model_id=self._model)
        ready = self._model in names or any(_same_base(self._model, n) for n in names)
        detail = "" if ready else f"model {self._model} is not pulled"
        return ProviderReadiness(ready=ready, detail=detail, model_id=self._model)

    async def generate(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        model = request.model_id or self._model
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": True,
            "options": {"num_ctx": self._num_ctx, "num_predict": request.max_output_tokens},
        }
        try:
            async with (
                self._client(streaming=True) as client,
                client.stream("POST", "/api/chat", json=payload) as resp,
            ):
                if resp.status_code != 200:
                    await resp.aread()
                    raise GenerationError(
                        GenerationErrorCode.provider_error,
                        f"ollama returned HTTP {resp.status_code}",
                    )
                yield GenStarted(self.provider_id, model, self.data_boundary.value)
                async for event in _parse_stream(resp):
                    yield event
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise GenerationError(
                GenerationErrorCode.provider_unavailable,
                f"ollama endpoint is unreachable: {type(exc).__name__}",
            ) from exc
        except httpx.HTTPError as exc:
            raise GenerationError(
                GenerationErrorCode.provider_error, f"ollama request failed: {type(exc).__name__}"
            ) from exc


async def _parse_stream(resp: httpx.Response) -> AsyncIterator[GenerationEvent]:
    async for line in resp.aiter_lines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("error"):
            raise GenerationError(GenerationErrorCode.provider_error, str(obj["error"]))
        content = (obj.get("message") or {}).get("content")
        if content:
            yield GenDelta(content)
        if obj.get("done"):
            usage = _usage(obj)
            yield GenUsage(usage)
            yield GenFinished(
                _FINISH_REASONS.get(obj.get("done_reason", "stop"), FinishReason.stop), usage
            )
            return


def _usage(obj: dict[str, object]) -> Usage:
    inp = obj.get("prompt_eval_count")
    out = obj.get("eval_count")
    inp_i = inp if isinstance(inp, int) else None
    out_i = out if isinstance(out, int) else None
    total = inp_i + out_i if inp_i is not None and out_i is not None else None
    return Usage(input_tokens=inp_i, output_tokens=out_i, total_tokens=total)


def _same_base(model: str, name: str) -> bool:
    """Match ignoring an implicit ``:latest`` tag (``llama3.1`` ~ ``llama3.1:latest``)."""
    return model.split(":", 1)[0] == name.split(":", 1)[0]


def build_ollama_provider(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> OllamaProvider:
    return OllamaProvider(
        base_url=settings.ollama_url,
        model=settings.ollama_chat_model,
        num_ctx=settings.ollama_num_ctx,
        max_output_tokens=settings.generation_max_output_tokens,
        transport=transport,
    )
