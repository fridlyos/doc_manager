# Phase 5.d — OpenAI Responses Adapter (opt-in external provider)

**Status:** ✅ complete · **Branch:** `phase-5-generation` · **Spec:** TECHSTACK §5.13, §12

The external `GenerationProvider`: an OpenAI adapter over the official SDK's
**Responses API**, stateless (`store=false`) and streaming, normalized into the
Phase 5.a event set. It is opt-in at every level — installed only via the `openai`
extra, registered only when configured, and usable only after the registry gate
(5.a) and the external-processing policy (5.c) both pass.

---

## 1. What it does

`OpenAIProvider` (`generation/openai_provider.py`) implements the provider protocol:

- **`generate(request)`** — `responses.create(model, instructions=system_prompt,
  input=user_prompt, max_output_tokens, store=False, stream=True)`. **No**
  `previous_response_id`, Conversations, background mode, hosted file search, web
  search, tools, or file uploads. Streaming events are normalized by their `type`:
  - `response.output_text.delta` → `GenDelta`
  - `response.refusal.delta` (accumulated) → terminal `GenRefusal` at completion
  - `response.incomplete` → `GenFinished(length)`
  - `response.completed` → `GenUsage` + `GenFinished(stop)`
  - `response.failed` / `error` → raised `GenerationError`
  `GenStarted` is emitted after the stream opens.
- **`readiness()`** — no key → not ready immediately; otherwise validates the model
  with `models.retrieve` and maps auth/other failures to a not-ready detail.
- **`secret_available()`** — `bool(read_openai_api_key())`.
- **`data_boundary = external`**, `capabilities.context_tokens = openai_context_tokens`.

## 2. Error mapping

| SDK exception | Normalized |
| --- | --- |
| `AuthenticationError` | `provider_authentication_failed` (401) |
| `RateLimitError` | `provider_rate_limited` (429) |
| `APITimeoutError` | `provider_timeout` (504) |
| `APIConnectionError` | `provider_unavailable` (503) |
| `APIError` (other) | `provider_error` (502) |
| stream `response.failed` / `error` | `provider_error` (502) |

`max_retries=0` on the client — no indefinite retry of a non-idempotent call; the
Ask layer's `stream_with_timeout` bounds the overall stream.

## 3. Secrets & opt-in

- The API key is read from `Settings.read_openai_api_key()` — a Docker-secret / env
  file injected into the **API** service only. It is never placed in PostgreSQL, the
  browser, logs, or Problem details.
- The `openai` package is an **optional extra**; `openai_provider.py` imports it
  lazily (only `TYPE_CHECKING` at module top), so `import doc_manager.generation`
  and the base install work without it.
- `build_registry` registers the adapter only when `openai_model` is set **and**
  `openai` is importable; if external is enabled but the extra/model is missing it
  logs a warning rather than failing. Registration ≠ usability — eligibility
  (opt-in + allowlist + secret) and policy still gate every call.

## 4. Config

Added `openai_context_tokens` (128000). Reuses `openai_model`,
`openai_api_key_file` / `read_openai_api_key`, `external_max_output_tokens`, and
`external_request_timeout_seconds`.

## 5. Verification

13 unit tests (offline; fake async client; real `openai` exception instances;
module `importorskip`s the extra so CI without it skips cleanly):

- identity/boundary/secret; stream normalization (deltas, usage, `stop`);
  **stateless safe payload** — asserts `store=False`, only `instructions`+`input`
  text sent, and none of `tools`/`previous_response_id`/`conversation`/`background`;
  refusal → `GenRefusal`; incomplete → `length`; stream failure → `provider_error`;
  the four SDK-error mappings; readiness ok / no-key / auth-failure; `build_registry`
  registers OpenAI when configured (still ineligible without a secret) and omits it
  without a model.

A real OpenAI live smoke is intentionally deferred to **5.h** (opt-in, skipped by
default) — it requires a key and egress. The adapter's normalization and error
mapping run against realistic event shapes and the real SDK exception types here.

Gate: full backend suite **205 pass** (192 prior + 13 new); ruff + mypy clean.

## 6. Follow-ups

- **5.e/5.f** — the RAG layer builds `GenerationRequest`; `/ask` maps these events
  to SSE, wraps the stream in `stream_with_timeout(external_request_timeout_seconds)`,
  runs the 5.c policy before any call, and flips the `external_boundary`
  attempted/occurred flags around the request.
- **5.h** — shared contract fixtures run Ollama and OpenAI through the same
  grounding/citation assertions; optional live OpenAI smoke.
