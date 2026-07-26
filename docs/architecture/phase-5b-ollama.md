# Phase 5.b — Ollama Adapter (default local provider)

**Status:** ✅ complete · **Branch:** `phase-5-generation` · **Spec:** TECHSTACK §5.13

The first concrete `GenerationProvider`: a local Ollama adapter that streams over
Ollama's HTTP API and normalizes its NDJSON chat stream into the Phase 5.a event
set. All prompt/answer content stays on the host — `data_boundary = local`, no
secret, no external transfer.

---

## 1. What it does

`OllamaProvider` (`generation/ollama.py`) implements the `GenerationProvider`
protocol:

- **`generate(request)`** — `POST /api/chat` with `stream: true` and
  `options: { num_ctx, num_predict }`. Each NDJSON line is normalized:
  `message.content` → `GenDelta`; the final `done` line → `GenUsage`
  (`prompt_eval_count`/`eval_count`) then `GenFinished` (`done_reason`
  `stop`→`stop`, `length`→`length`, else `stop`). `GenStarted` is emitted only
  after the connection opens and returns 200, so a connect failure never produces
  a spurious start.
- **`readiness()`** — `GET /api/tags`; ready iff the configured model (exact, or
  tag-insensitive base match) is pulled. Never downloads a model.
- **`secret_available()`** — always `True` (local needs no credential).
- **`data_boundary = local`**, `capabilities.context_tokens = ollama_num_ctx`.

`build_registry(settings)` now always registers Ollama; eligibility still gates
external providers (Ollama, being local, is always eligible).

## 2. Error mapping

| Condition | Result |
| --- | --- |
| Connect refused / connect timeout | `GenerationError(provider_unavailable, 503)` |
| Non-200 response, other HTTP error | `GenerationError(provider_error, 502)` |
| Inline `{"error": …}` in the stream | `GenerationError(provider_error, 502)` |
| Overall deadline (via `stream_with_timeout`, 5.a) | `provider_timeout, 504` |

Timeouts: a short connect timeout surfaces an unreachable endpoint fast; the read
timeout is unbounded during streaming because the Ask layer (5.f) bounds the whole
stream. Cancellation tears the httpx stream down through normal async-generator
cleanup.

## 3. Testability

The adapter accepts an optional `httpx.AsyncBaseTransport`, so tests inject
`httpx.MockTransport` — no live Ollama, no new dependency (no `respx`).

## 4. Config

Added `ollama_num_ctx` (8192) — the advertised local context window; drives
evidence budgeting (5.e) and is passed to Ollama as `options.num_ctx`. Reuses
`ollama_url`, `ollama_chat_model`, and `generation_max_output_tokens`.

## 5. Verification

- **9 unit tests (offline, `MockTransport`):** provider identity/boundary/secret;
  NDJSON stream → ordered `GenStarted`/`GenDelta`/`GenUsage`/`GenFinished` with
  parsed usage; `length` finish mapping; unreachable → `provider_unavailable`;
  HTTP 500 and inline error → `provider_error`; readiness reports model presence /
  absence / unreachable; `build_registry` includes and gates Ollama.
- **Live smoke (manual, real Ollama on `host.docker.internal:11434`):** readiness
  correctly reported the default `llama3.1:8b` as *not pulled*; against an
  installed model the stream produced `GenStarted → … → GenUsage(48/40/88) →
  GenFinished`, confirming end-to-end normalization against the real endpoint.

Gate: full backend suite **182 pass** (173 prior + 9 new); ruff + mypy clean.

## 6. Follow-ups

- **5.c** external-processing policy (deny default, per-source allow) — Ollama is
  local so it bypasses external checks, but the policy layer must treat it as the
  no-transfer baseline.
- **5.e** RAG builds `GenerationRequest.system_prompt`/`user_prompt`; this adapter
  already splits them into Ollama system/user messages.
- **5.f** `/ask` + `/ask/stream` map these events to SSE and wrap the stream in
  `stream_with_timeout`.
