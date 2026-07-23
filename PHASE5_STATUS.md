# Phase 5 Progress Status

**Branch:** `phase-5-generation` (cut from `main` @ `fa48b24`, Phase 4 merged via PR #5).

Scope = TECHSTACK §14 "Phase 5: Pluggable Local/External RAG Generation".

Phase 5 turns retrieved evidence (Phase 4) into grounded, cited answers through a
provider-neutral `GenerationProvider` interface, with a local Ollama default and an
opt-in external OpenAI adapter behind a fail-closed policy. **No indexing/retrieval
changes** — this layer consumes the existing `RetrievalService`.

## Deliverables

| # | Deliverable | Status |
| --- | --- | --- |
| 5.a | Provider interface + registry, health, streaming events, cancellation, usage, bounded timeouts | **✅ complete** |
| 5.b | Ollama adapter (default local provider) | **✅ complete** |
| 5.c | Deployment/source external-processing policy, `deny` default, no auto-fallback | **✅ complete** |
| 5.d | OpenAI Responses adapter (official SDK, `store=false`, stateless streaming, Docker-secret auth) | **✅ complete** |
| 5.e | Provider-neutral evidence selection, grounded prompts, server-owned citation mapping | ⬜ not started |
| 5.f | State handling: insufficient-evidence, provider-unavailable, auth, rate-limit, policy-denied, refusal | ⬜ not started |
| 5.g | Ask UI: provider selection, streamed answer/evidence cards, Local/External badge, external preview/confirm | ⬜ not started |
| 5.h | Provider contract tests + optional synthetic OpenAI live smoke | ⬜ not started |

## Exit criteria (whole phase)

1. Answers use retrieved evidence and expose paths/pages/snippets.
2. A model cannot invent a clickable citation path (server-owned citation mapping).
3. Local mode works with outbound provider access disabled and contacts no cloud service.
4. External mode sends only allowed question/evidence text — never paths, file names,
   original files, or denied-source content.
5. An unavailable provider produces an explicit error and never triggers a different
   provider automatically.
6. OpenAI and Ollama pass the same grounding/citation contract fixtures.

## Already in place (reused, not rebuilt)

- **Retrieval (Phase 4):** `RetrievalService.search` → ranked chunks with snippet, page
  range, and current display paths resolved from PostgreSQL. Ask builds evidence from
  this; search stays usable with no provider (TECHSTACK 5.12).
- **Config (`core/config.py`):** `generation_provider`, `external_llm_enabled`,
  `ollama_url`/`ollama_chat_model`, `openai_model`/`openai_api_key_file` +
  `read_openai_api_key()`, `external_provider_allowlist`, `external_source_default`,
  `external_max_evidence_tokens`, `external_max_output_tokens`,
  `external_request_timeout_seconds`. Validation already rejects `openai` without
  `external_llm_enabled`, and requires a model id.
- **Source-level policy:** `SourceLocation.external_generation_policy` (`deny`/`allow`)
  exists from Phase 2 — the per-source gate for external transfer.
- **Health:** `_check_ollama` / `_check_openai` readiness components exist; readiness
  already models `search_only` when no provider is up.
- **Envelope/Problem/idempotency/SSE-free plumbing** from Phases 1–2.
- **Optional dep group:** `openai` is declared in `pyproject.toml` under
  `[project.optional-dependencies]` (installed only when the adapter is enabled).

## Contract anchors (docs/api/contracts.md §8, TECHSTACK §5.13, §12)

- Common request (`§8.1`): `{ question, provider_id, external_processing_acknowledged,
  filters, retrieval, generation }`. Stateless — no conversation/response id.
- Normal result (`§8.2`): discriminated `status` = `completed | insufficient_evidence |
  refused`; `provider`, `data_boundary` (with `external_payload` counters), `retrieval`,
  `citations[]` (server-resolved paths/pages/snippet/availability), `finish_reason`,
  `usage`, `timing`, `warnings`.
- Streaming (`§8.3`): `POST /ask/stream` SSE with normalized events `ask.started →
  retrieval.completed → [generation.started → answer.delta* / citation.resolved* /
  ask.warning*] → ask.result | ask.error`. Incrementing `id`; `: keep-alive` ≥ every 15s;
  provider SDK event names never pass through; one terminal event; no `[DONE]`; no resume.
- External boundary (`§12`, TECHSTACK 5.13): outbound payload = question + grounding
  instructions + evidence text + opaque citation ids **only**. Never paths, file names,
  document/source/catalog ids, tags, or original files. Fail closed on any denied source;
  no automatic fallback.

---

## Completed work

### 5.a — Provider interface, registry, normalized streaming ✅ (2026-07-23)

Delivered `doc_manager/generation/` (foundation only — no adapters/endpoints):
`events.py` (normalized `GenStarted/GenDelta/GenUsage/GenFinished/GenRefusal` +
`FinishReason`, `Usage`), `base.py` (`GenerationProvider` protocol, `DataBoundary`,
`ProviderCapabilities`, `GenerationRequest`, `ProviderReadiness`; `secret_available`
gate hook; cooperative-cancel semantics), `errors.py` (`GenerationError` + code→HTTP
taxonomy, per-code retryability), `timeout.py` (`stream_with_timeout` → maps expiry
to `provider_timeout`, tears down the stream), `registry.py` (`ProviderRegistry`:
`get`→`unknown_provider`, `require_eligible`→`provider_unavailable`, static
eligibility gate — local always, external requires flag+allowlist+secret, **no
fallback**; `build_registry` empty until 5.b/5.d). Config: `generation_max_output_tokens`,
`generation_request_timeout_seconds`, `sse_keepalive_seconds`. 10 unit tests; full
backend suite **173 pass**; ruff/mypy clean. **Full report:
`docs/architecture/phase-5a-generation-foundation.md`.**

Progresses exit criterion 5 (no automatic fallback — enforced by the registry gate).

### 5.b — Ollama adapter (default local provider) ✅ (2026-07-23)

Delivered `generation/ollama.py`: `OllamaProvider` streams `POST /api/chat`
(`stream:true`) and normalizes the NDJSON into `GenStarted → GenDelta* → GenUsage
→ GenFinished` (usage from `prompt_eval_count`/`eval_count`; `done_reason` →
`FinishReason`). `readiness()` checks `/api/tags` for the pulled model (never
downloads); `secret_available` = True; `data_boundary = local`. Errors map:
connect → `provider_unavailable`, HTTP/inline error → `provider_error`. Registered
in `build_registry` (always present; eligibility still gates external). Adapter
takes an injectable `httpx` transport so tests use `httpx.MockTransport` (no
`respx` dep). Config adds `ollama_num_ctx` (8192). 9 unit tests; full backend suite
**182 pass**; ruff/mypy clean. Live-smoke verified against real Ollama on
`host.docker.internal:11434` (readiness + streamed events + usage). **Full report:
`docs/architecture/phase-5b-ollama.md`.**

Progresses exit criterion 3 (local mode contacts no cloud service).

### 5.c — External-processing policy + data-boundary accounting ✅ (2026-07-23)

Delivered `generation/policy.py` (`evaluate_external_policy` → `PolicyOutcome`:
local bypass; external-disabled denied; **any** denied evidence source → denied
with count; all-allow-without-ack → confirmation_required; all-allow-with-ack →
allowed — deny default, fail closed, no fallback) and `generation/boundary.py`
(`DataBoundaryReport`/`ExternalPayload` per §8.2 with **structurally-zero**
metadata counters — paths/file_names/tags/catalog_ids/original_files; builders
`local_boundary`, `external_boundary`, `confirmation_summary`). Added
`external_policy_denied` (403) to the error taxonomy. 10 unit tests; full backend
suite **192 pass**; ruff/mypy clean. **Full report:
`docs/architecture/phase-5c-external-policy.md`.**

Progresses exit criterion 4 (external mode sends only allowed text — enforced by
the policy gate + zero-metadata boundary counters) and 5 (no fallback).

### 5.d — OpenAI Responses adapter (opt-in external) ✅ (2026-07-23)

Delivered `generation/openai_provider.py`: `OpenAIProvider` over the Responses API
— `responses.create(instructions, input, max_output_tokens, store=False,
stream=True)`, no tools/previous_response_id/conversation/background. Streaming
events normalized by `type` (`output_text.delta`→`GenDelta`; `refusal.delta`→
`GenRefusal`; `incomplete`→length; `completed`→usage+stop; `failed`/`error`→
raise). SDK errors mapped (auth/rate-limit/timeout/connection/other →
`provider_*`); `max_retries=0`. Key from `read_openai_api_key()` (Docker
secret/API-service only), lazy `openai` import (base install works without the
extra), registered in `build_registry` only when the extra is present + model
configured (eligibility + policy still gate use). Config adds
`openai_context_tokens`. 13 unit tests (fake client + real SDK exceptions;
`importorskip`); full backend suite **205 pass**; ruff/mypy clean. Live smoke
deferred to 5.h. **Full report: `docs/architecture/phase-5d-openai.md`.**

---

## Planned work

### 5.a — Provider interface, registry, normalized streaming (foundation)

New `doc_manager/generation/` (pure interfaces + registry; adapters in submodules):
- `base.py` — `GenerationProvider` protocol:
  - `provider_id: str`, `data_boundary: "local" | "external"`, `capabilities`
    (context/output token limits, streaming).
  - `async readiness() -> ProviderReadiness` (model/endpoint validation).
  - `async generate(request: GenerationRequest) -> AsyncIterator[GenerationEvent]` —
    stateless streamed generation with a bounded per-request timeout and cooperative
    cancellation.
- `events.py` — **normalized** provider events (SDK types never leak):
  `GenStarted(provider, model)`, `GenDelta(text)`, `GenUsage(input/output/total)`,
  `GenFinished(reason: stop|length|refusal|content_filter)`, `GenRefusal(message)`,
  `GenError(code, message, retryable)`. The Ask service maps these to the SSE
  `ask.*` events and to the normal result.
- `registry.py` — `ProviderRegistry`: build enabled adapters from `Settings`; expose
  `get(provider_id)` and `list()`; a provider is **ready** only when enabled +
  (for external) allowlisted + secret present + `external_llm_enabled`. No fallback.
- `errors.py` — provider error codes: `provider_unavailable`, `provider_timeout`,
  `provider_authentication_failed`, `provider_rate_limited`, `provider_refused`,
  `provider_error`. Mapped to Problem/HTTP (401/429/503/504/…) and to `ask.error`.
- Config: add `generation_max_output_tokens`, `generation_request_timeout_seconds`
  (local), and a keep-alive interval for SSE.

Tests: registry readiness matrix (local up, external gated by flag/allowlist/secret);
no-fallback assertion; timeout wraps a slow adapter; event normalization shape.

### 5.b — Ollama adapter (default local)

`generation/ollama.py`:
- Streams from the native Windows Ollama endpoint (`ollama_url`, `/api/chat` with
  `stream=true`) via `httpx.AsyncClient`. `data_boundary = "local"`.
- Normalizes NDJSON chunks → `GenDelta`/`GenUsage`/`GenFinished`; maps connection
  errors → `provider_unavailable`, deadline → `provider_timeout`.
- `readiness()` checks the model is present (`/api/tags`); never downloads a model.
- Keeps all prompt/answer content on the local host.

Tests: streamed deltas assembled in order; usage parsed; unavailable endpoint →
`provider_unavailable`; timeout honored. HTTP mocked with `respx` (no live Ollama).

### 5.c — External-processing policy (fail closed, no fallback)

`generation/policy.py` — `evaluate_external_policy(settings, provider, evidence_sources)`:
- External generation allowed only when **all** hold (TECHSTACK 5.13):
  1. `external_llm_enabled` is true;
  2. the adapter is on `external_provider_allowlist` and has a valid secret;
  3. **every** evidence-bearing source location is `external_generation_policy = allow`;
  4. the request selected/accepted the external provider
     (`external_processing_acknowledged = true`).
- Missing acknowledgment (but otherwise allowed) → `external_confirmation_required`
  safe response (counts only: evidence blocks/characters; zero metadata) — **no provider
  call** (contract §8.1).
- Any denied source → fail closed with `external_policy_denied`; evidence is **not**
  silently dropped and no other provider is chosen.
- Local providers bypass external checks (boundary = local).

Tests: allow-all path; one denied source → denied; flag off → denied; ack absent →
confirmation-required with counts and no provider call; local provider unaffected.

### 5.d — OpenAI Responses adapter (opt-in external)

`generation/openai_provider.py` (import guarded behind the `openai` extra):
- Official SDK, **Responses API**, `store=false`, streaming; no `previous_response_id`,
  Conversations, background mode, hosted file search, web search, tools, or file uploads.
- Operator-configured `openai_model`; key from `read_openai_api_key()` (Docker secret /
  env injected into the API service only) — never DB/UI/logs.
- Normalizes typed Responses stream events → the neutral event set; maps
  `AuthenticationError → provider_authentication_failed (401)`,
  `RateLimitError → provider_rate_limited (429)`, timeouts → `provider_timeout (504)`.
  `data_boundary = "external"`.
- Bounded timeout (`external_request_timeout_seconds`) and no indefinite retry of
  non-idempotent calls.

Tests: SDK mocked — event normalization; auth/rate-limit/timeout mapping; assert the
outbound request carries only question + grounding + evidence + aliases (no paths/names/
ids); `store=false` set. Live smoke is 5.h (opt-in, skipped by default).

### 5.e — Evidence selection, grounded prompt, server-owned citations (RAG core)

`generation/rag.py` — `AskService`:
- **Evidence selection:** take retrieval hits, collapse repeated/overlapping chunks,
  cap evidence per content object, and fit a **token budget** for the selected model
  (context limit − output reservation; external also bounded by
  `external_max_evidence_tokens`). Assign opaque aliases `E1, E2, …`.
- **Grounded prompt:** system instructions (answer only from evidence; cite with the
  given aliases; declare insufficient evidence if unsupported; treat document text as
  untrusted data, not instructions) + numbered evidence blocks + the question.
- **Server-owned citation mapping:** the provider sees aliases only. After generation,
  map used aliases → citations (chunk_id, page range, snippet, server-resolved paths +
  availability from PostgreSQL). Convert answer markers to ordinals `[1]`; drop unknown
  aliases and add `unknown_provider_citation_removed` to `warnings`. A provider-produced
  path is **never** trusted or displayed (exit criteria 2).
- Empty/weak evidence → `insufficient_evidence` with **no provider call**.

Tests: token-budget trimming + per-content cap; alias round-trip; unknown-alias removal
warning; provider-invented path ignored; insufficient-evidence short-circuit.

### 5.f — Ask endpoints + state handling

`api/v1/routes/ask.py`:
- `POST /api/v1/ask` — normal single result (`§8.2`).
- `POST /api/v1/ask/stream` — SSE (`§8.3`): validate + readiness + policy **before**
  committing SSE headers (pre-stream failures are ordinary Problem responses);
  then `ask.started → retrieval.completed → [generation.started → answer.delta* /
  citation.resolved* / ask.warning*] → ask.result | ask.error`. Incrementing `id`,
  `: keep-alive` heartbeat, cooperative cancel on disconnect, no fallback, no resume.
- `GET /api/v1/system/providers` + `POST /api/v1/system/providers/{id}/test`
  (fixed synthetic text; never sends document content).
- Problem codes: `insufficient_evidence` (200 result, not an error),
  `provider_unavailable` (503), `provider_authentication_failed` (401),
  `provider_rate_limited` (429), `external_policy_denied` (403),
  `external_confirmation_required` (safe 2xx with counts), `provider_timeout` (504),
  and a `refused` result (200, `status: refused`).
- **Optional audit table (migration 0005):** metadata-only `ask_requests`
  (provider, model, data_boundary, timing, usage, finish_reason, request_id — **no**
  question/answer/evidence text) for observability. *Open decision — may stay log-only.*

Tests: PG-backed — each state produces the right status/problem; SSE event order and
single terminal event; provider-unavailable never invokes another provider; insufficient
evidence emits no `generation.started`.

### 5.g — Ask UI

Frontend `AskPage`:
- Question box + **provider selector** (from `/system/providers`); a persistent
  **Local/External badge** reflecting the selected provider's data boundary.
- Streamed answer via streaming `fetch` (not `EventSource`) consuming the SSE events;
  render `answer.delta` progressively, then reconcile with the authoritative
  `ask.result`.
- **Evidence cards** (citations) with display_path, page range, snippet, availability;
  answer markers link to cards.
- **External preview/confirmation:** on `external_confirmation_required`, show the
  counts-only summary (evidence blocks/characters, zero metadata) and require explicit
  confirm before re-sending with `external_processing_acknowledged = true`.
- States: insufficient-evidence, provider-unavailable, auth, rate-limit, policy-denied,
  refusal, timeout. `client.ts` `ask()`/`askStream()` + `fetchProviders()`.

Tests (vitest): provider select + badge; streamed delta rendering + terminal reconcile;
citation cards; external confirmation gate; error states.

### 5.h — Provider contract tests + optional OpenAI live smoke

- **Shared contract fixtures** run against **both** adapters (Ollama mocked, OpenAI
  mocked): same grounding-prompt shape, same citation-mapping behavior, same normalized
  events, same insufficient/refusal handling (exit criterion 6).
- **Optional live smoke** (`@pytest.mark.live_openai`, skipped unless a key + opt-in env
  are present): one synthetic question over fixed synthetic evidence; asserts a grounded,
  cited answer and that no document content beyond allowed evidence is sent.

---

## Security posture (must hold throughout)

- **No silent egress.** External generation requires deployment opt-in + allowlist +
  secret + every-source `allow` + explicit acknowledgment; otherwise it fails closed.
- **Server-owned citations.** Providers receive opaque aliases only; paths/pages resolve
  locally from PostgreSQL; provider-produced paths are ignored (exit criteria 2, 4).
- **Boundary accounting.** The response's `data_boundary.external_payload` counts what was
  actually sent; metadata counters (paths/file names/tags/catalog ids/original files)
  stay zero. Once the external HTTP write begins, `external_request_attempted` and
  `external_transfer_occurred` are both true (conservative), per §8.2.
- **Secrets.** Provider keys come only from env/Docker secrets read by the API service;
  never in PostgreSQL, the browser, logs, or Problem details.
- **Prompt injection.** Document evidence is framed as untrusted data, not instructions.
- **No history.** Question/answer content is not persisted unless a retention policy is
  explicitly enabled; the optional audit table stores metadata only.

## Open decisions (resolve during 5.a / 5.f)

1. **SSE implementation** — hand-rolled `StreamingResponse` (no new dep, full control of
   `id`/heartbeat/ordering) vs. `sse-starlette`. Leaning hand-rolled to keep the
   dependency surface minimal and match the exact contract.
2. **Ask audit table vs. log-only** — a metadata-only `ask_requests` table (migration
   0005) for observability, or structured logs only. Leaning a small table (no content),
   since §5.13 says "record provider/model, timing, request id, token usage."
3. **Evidence token budgeting** — reuse the chunking tokenizer heuristic for the budget
   vs. a per-model tokenizer. Leaning the existing heuristic for v1, with the external
   cap from `external_max_evidence_tokens`.
4. **Ollama endpoint** — `/api/chat` (message roles) vs. `/api/generate` (single prompt).
   Leaning `/api/chat` for a clean system/user split of grounding vs. question.
5. **Provider/model selection surface** — does `GET /system/providers` enumerate models
   per provider, or only the configured active model? Leaning active-model-only in v1
   (contract: "a model exposed by that enabled provider configuration").

## New dependencies

- `openai` (already declared as an optional extra) — installed only when the OpenAI
  adapter is enabled; the adapter import is guarded so the base install stays lean.
- No mandatory new runtime deps for the local path (Ollama uses `httpx`, already present).
- Possibly `sse-starlette` — only if open decision #1 chooses it over hand-rolled SSE.

## Ops notes

- **Local Ask** needs native Windows Ollama reachable at `ollama_url`
  (`host.docker.internal:11434`) with the configured model pulled — an explicit operator
  action; the adapter never pulls.
- **External Ask** needs `DOCMAN_EXTERNAL_LLM_ENABLED=true`, the provider on the
  allowlist, and the OpenAI key mounted as a Docker secret into the **API** service only.
- Search/retrieval remains fully functional with no provider configured — Ask degrades to
  explicit `provider_unavailable`, retrieval does not.
- Contract/unit tests mock providers (no live calls); the OpenAI live smoke is opt-in and
  excluded from default/CI runs.
