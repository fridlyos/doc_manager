# Phase 5.a — Generation Provider Foundation

**Status:** ✅ complete · **Branch:** `phase-5-generation` · **Spec:** TECHSTACK §5.13, §12; contract §8

The provider-neutral core the rest of Phase 5 builds on: a `GenerationProvider`
interface, a normalized streaming event model, a provider-error taxonomy, a
bounded-timeout wrapper, and a registry whose eligibility gate enforces the
external opt-in with **no automatic fallback**. Adapters (5.b Ollama, 5.d OpenAI)
and the Ask service (5.e/5.f) plug into this; no adapter or endpoint ships here.

---

## 1. Why a foundation-only step

Every downstream piece — both adapters, the RAG/citation layer, the SSE endpoint —
depends on one interface and one event vocabulary. Fixing those first means the
adapters normalize to a single shape and the Ask service maps one event set to the
public `ask.*` SSE events, so provider SDK types never leak (contract §8.3).

## 2. Module layout

```
backend/src/doc_manager/generation/
├── __init__.py     public exports
├── events.py       normalized stream events (Started/Delta/Usage/Finished/Refusal)
├── base.py         GenerationProvider protocol, DataBoundary, capabilities, request
├── errors.py       GenerationError + code→HTTP taxonomy
├── timeout.py      stream_with_timeout (bounded streaming)
└── registry.py     ProviderRegistry + eligibility gate + build_registry
```

Tests: `backend/tests/unit/test_generation_foundation.py` (10 tests).

## 3. Normalized events (`events.py`)

Adapters emit only these; the Ask service maps them to SSE and to the result:

- `GenStarted(provider_id, model_id, data_boundary)` — exactly once, first.
- `GenDelta(text)` — a non-empty answer fragment; concatenated in order.
- `GenUsage(Usage)` — token counts (any field nullable; not comparable across
  providers).
- `GenFinished(FinishReason, usage?)` — terminal success. `FinishReason` ∈
  `stop | length | refusal | content_filter`.
- `GenRefusal(message)` — terminal; the model declined (a 200 `status: refused`,
  not an error).

A well-formed stream is `GenStarted → GenDelta* → GenUsage? → GenFinished` (or
`GenRefusal`). Transport/provider faults are **raised**, not emitted.

## 4. Provider interface (`base.py`)

```python
class GenerationProvider(Protocol):
    provider_id: str
    data_boundary: DataBoundary            # local | external
    capabilities: ProviderCapabilities     # context_tokens, max_output_tokens, streaming
    async def readiness(self) -> ProviderReadiness: ...
    def secret_available(self, settings: Settings) -> bool: ...
    def generate(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]: ...
```

- **Stateless**: each `generate` is an independent streamed request (no
  conversation/response id).
- **Cancellation is cooperative**: closing the returned iterator (`aclose`) stops
  the provider work; the Ask service does this on client disconnect.
- `GenerationRequest` carries the finished `system_prompt` (grounding instructions
  + numbered evidence, built by the 5.e RAG layer), the `user_prompt` (question),
  `max_output_tokens`, and an optional `model_id`. The provider never resolves
  paths or citations — that stays server-side (exit criterion 2).
- `secret_available` lets the registry gate external providers without the base
  layer knowing any provider's secret mechanics.

## 5. Error taxonomy (`errors.py`)

`GenerationError(code, message, retryable?)` with `http_status`:

| Code | HTTP | Default retryable |
| --- | --- | --- |
| `unknown_provider` | 404 | no |
| `provider_unavailable` | 503 | yes |
| `provider_timeout` | 504 | yes |
| `provider_authentication_failed` | 401 | no |
| `provider_rate_limited` | 429 | yes |
| `provider_error` | 502 | no |

Semantic outcomes (refusal, insufficient evidence) are **not** errors — they are
200 results carried as events/status. Retryability defaults per code and can be
overridden per instance.

## 6. Bounded streaming (`timeout.py`)

`stream_with_timeout(events, *, timeout_seconds)` wraps a provider stream in an
overall deadline (`asyncio.timeout`). On expiry it tears down the underlying
stream (`aclose`, if supported) and raises `GenerationError(provider_timeout)`.
Consumer cancellation (disconnect) propagates through normal async-generator
teardown — no provider switch, ever.

## 7. Registry + eligibility gate (`registry.py`)

`ProviderRegistry(providers)` holds adapters by id and decides **config-level
eligibility** (no network — that is `readiness()`):

- **local** provider → always eligible;
- **external** provider → eligible only when `external_llm_enabled` **and** on
  `external_provider_allowlist` **and** `secret_available(settings)`.

`get(id)` raises `unknown_provider` for an unknown id. `require_eligible(id)`
raises `provider_unavailable` for a known-but-not-enabled provider — **never**
returns a different provider (exit criterion 5: no automatic fallback).
`build_registry(settings)` assembles the deployment's providers; it is empty in
5.a and gains the Ollama adapter (5.b) and OpenAI adapter (5.d) as they land.

## 8. Config additions

`generation_max_output_tokens` (1200), `generation_request_timeout_seconds`
(120, local path), `sse_keepalive_seconds` (15, for the §8.3 keep-alive). The
external path continues to use the existing `external_*` settings.

## 9. Verification

10 unit tests (offline, fake adapters): event/enum shapes; error code→HTTP and
retryability (incl. explicit override); timeout maps a slow stream to
`provider_timeout` and passes a fast stream through; registry unknown-provider,
local-always-eligible, external gated by flag/allowlist/secret, **no-fallback**
selection is explicit, duplicate-id rejected, and `build_registry` empty until
adapters land.

Gate: full backend suite **173 pass** (163 prior + 10 new); ruff + mypy clean.

## 10. Follow-ups (rest of Phase 5)

- **5.b** Ollama adapter (local, `httpx` NDJSON stream) registered in
  `build_registry`.
- **5.c** external-processing policy (deny default, per-source `allow`, fail
  closed) — the registry gate is the config half; policy adds the per-request,
  per-evidence-source half.
- **5.d** OpenAI Responses adapter (guarded import), using `secret_available`
  against the Docker-secret key.
- **5.e/5.f** RAG evidence selection + grounded prompts + server-owned citations,
  and the `/ask` + `/ask/stream` endpoints mapping these events to SSE.
