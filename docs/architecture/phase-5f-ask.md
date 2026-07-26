# Phase 5.f — Ask Endpoints, SSE Streaming, State Handling, Provider Discovery

**Status:** ✅ complete · **Branch:** `phase-5-generation` · **Spec:** TECHSTACK §5.13; contract §8

The orchestration step: `AskService` ties Phase 4 retrieval and the Phase 5
provider/policy/RAG pieces into one grounded, cited answer, exposed as `POST /ask`
(normal §8.2 result) and `POST /ask/stream` (normalized §8.3 SSE), plus provider
discovery. Every state — insufficient evidence, provider unavailable, external
policy denied/confirmation, refusal, timeout — has a defined outcome, and no
provider is ever swapped in automatically.

---

## 1. `AskService` (`generation/ask.py`)

`ask()` (normal) and `ask_stream()` (events) share one pipeline:

1. **retrieve** — `RetrievalService.search` with the request's filters/top_k/threshold.
2. **select evidence** — `select_evidence` under the token budget derived from the
   provider's context window minus the output reservation (external also capped by
   `external_max_evidence_tokens`), per-content and block caps.
3. **insufficient short-circuit** — empty evidence → `insufficient_evidence`, the
   provider is **never called**.
4. **external policy** (external providers only) — resolve each evidence source's
   `external_generation_policy` from PostgreSQL and run `evaluate_external_policy`:
   `denied` → `GenerationError(external_policy_denied)`; `confirmation_required` →
   a counts-only result with **no provider call**; `allowed` → proceed.
5. **generate** — `build_grounded_prompt` → `provider.generate` wrapped in
   `stream_with_timeout` (local vs external timeout). Deltas accumulate; usage and
   finish reason are captured; a `GenRefusal` yields `refused`; a model
   `INSUFFICIENT_EVIDENCE` yields `insufficient_evidence`.
6. **map citations** — `map_citations` rewrites `[E#]`→`[#]`, builds server-owned
   `Citation`s, and drops invented aliases with a warning.
7. **result** — the §8.2 `AskResult` with `provider`, `data_boundary` report,
   `retrieval` counts, citations, usage, timing, and warnings.

`ask_stream()` emits the same outcome as events: `ask.started` →
`retrieval.completed` → (`generation.started` → `answer.delta*` /
`citation.resolved*` / `ask.warning*`) → `ask.result`, or a terminal error.

## 2. Endpoints (`api/v1/routes/ask.py`)

- **`POST /api/v1/ask`** — validates the body, selects the provider
  (`require_eligible` → Problem on failure), runs `AskService.ask`, and returns the
  §8.2 result in the standard envelope. `GenerationError` (policy/provider) maps to
  the right Problem status.
- **`POST /api/v1/ask/stream`** — does validation + provider selection **before**
  the `StreamingResponse` so those are ordinary Problems (headers not yet
  committed); then streams SSE frames. An in-stream `GenerationError` becomes a
  single terminal `ask.error` event. Frames carry an incrementing `id`, the common
  fields (`stream_version`, `sequence`, `request_id`, `ask_id`, `occurred_at`), and
  provider-neutral event names — SSE framing is hand-rolled (`api/sse.py`) to match
  the contract exactly.
- **`GET /api/v1/system/providers`** — lists providers with `data_boundary` and
  config-level `eligible`.
- **`POST /api/v1/system/providers/{id}/test`** — a live `readiness()` probe; sends
  no document content.

## 3. State → response mapping

| State | `/ask` | `/ask/stream` |
| --- | --- | --- |
| completed | 200 result `status: completed` | `ask.result` |
| insufficient evidence | 200 `insufficient_evidence` (no provider call) | `ask.result` |
| model refusal | 200 `refused` | `ask.result` |
| external confirmation | 200 `external_confirmation_required` + counts | `ask.result` |
| external policy denied | 403 `external_policy_denied` | `ask.error` |
| provider unavailable / unknown | 503 / 404 | ordinary Problem (pre-stream) |
| auth / rate-limit / timeout | 401 / 429 / 504 | `ask.error` |

## 4. Security posture

- Provider selection has **no fallback** — an ineligible provider is an explicit
  error, never a silent switch.
- External transfer is gated by the 5.c policy on the *actual* evidence sources;
  the `data_boundary` report's metadata counters stay zero, and
  `external_transfer_occurred` is set conservatively once generation is attempted.
- Citations are server-owned; the provider sees only aliases + evidence text.
- Provider `test` and readiness never send document content.

## 5. Verification

- **AskService unit (8 tests, offline):** completed-with-citation; insufficient
  (no provider call); model-declared insufficient; refusal; unknown-alias warning;
  external denied (raises, no call); external confirmation (no call, counts);
  external allowed (transfer reported, **zero metadata**).
- **Endpoint unit (8 tests, injected fakes):** `/ask` result envelope; unknown →
  404; ineligible → 503; blank question → 422; `/ask/stream` **ordered events +
  incrementing ids + single terminal + full result**; pre-stream provider error is
  an ordinary Problem (not SSE); `/system/providers` list + eligibility; provider
  `test` probe + unknown → 404.

The fakes emit the exact normalized event types the Ollama (5.b) and OpenAI (5.d)
adapters were verified to produce against real endpoints, so the orchestration is
tested against the real event contract. A live end-to-end Ask through real Ollama
was run in 5.b for the adapter; re-running it here was skipped because the local
Ollama endpoint was unreachable this session.

Gate: full backend suite **234 pass** (218 prior + 16 new); ruff + mypy clean.

## 6. Known limitations / follow-ups

- The SSE keep-alive helper exists but no idle-heartbeat task is wired; a long
  idle gap (rare for a streaming answer) would not emit `: keep-alive`. Wiring a
  heartbeat is a small follow-up.
- **5.g** Ask UI consumes these endpoints (provider select, streamed answer +
  evidence cards, Local/External badge, external confirmation gate).
- **5.h** shared contract fixtures across both adapters + optional OpenAI live smoke.
