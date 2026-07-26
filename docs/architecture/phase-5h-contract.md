# Phase 5.h — Provider Contract Fixtures & Optional Live Smoke

**Status:** ✅ complete · **Branch:** `phase-5-generation` · **Spec:** TECHSTACK §5.13 (exit criterion 6)

The step that proves both providers honour one contract: the same scripted model
answers are driven through `AskService` for **both** the Ollama adapter and the
OpenAI adapter, and must produce identical grounding/citation outcomes. Closes
Phase 5.

---

## 1. Shared fixtures (`tests/unit/test_provider_contract.py`)

A single parametrization runs every scenario against two adapter factories:

- **Ollama** — a real `OllamaProvider` with an `httpx.MockTransport` that returns
  the scripted answer as NDJSON chat chunks.
- **OpenAI** — a real `OpenAIProvider` with a fake SDK client whose
  `responses.create` yields the scripted answer as `response.output_text.delta`
  events + `response.completed`.

Both are fed the **same answer strings** and driven through the same `AskService`
(with a fake retrieval returning fixed evidence; the external source-policy lookup
is stubbed to `allow` for OpenAI). The assertions are provider-agnostic:

| Scenario | Assertion (identical for both) |
| --- | --- |
| completed + citation | `status=completed`; `[E1]`→`[1]`; one citation with the server-resolved path; no warnings |
| ordinal by appearance | repeated `[E1]` → one citation, both markers `[1]` |
| invented alias | `[E9]` dropped; `unknown_provider_citation_removed` warning; only `[E1]` cited |
| model-declared insufficient | `status=insufficient_evidence`; `answer=None`; `invoked=True` |
| usage captured | `usage.total_tokens == 26` |

Only the **data boundary** differs by design — `local` for Ollama, `external` for
OpenAI — asserted per parameter. Everything the model-facing contract governs
(grounding prompt, alias rewriting, citation records, insufficiency, usage) is
identical, satisfying **exit criterion 6**.

## 2. Optional OpenAI live smoke

`test_openai_live_smoke` is gated by `DOCMAN_OPENAI_LIVE=1` + `OPENAI_API_KEY`
(skipped by default, excluded from CI). When enabled it builds the real
`OpenAIProvider`, asks a synthetic question over fixed synthetic evidence, and
asserts a grounded result with `classification == external` and **zero** metadata
counters (`paths_sent`/`file_names_sent` == 0) — confirming the boundary holds
against the real API without sending document metadata.

## 3. Verification

10 contract tests (5 scenarios × 2 providers) + 1 skipped live smoke. Full backend
suite **244 pass, 1 skipped**; ruff + mypy clean.

## 4. Phase 5 complete

All deliverables 5.a–5.h are done. Exit criteria:

1. **Answers use retrieved evidence and expose paths/pages/snippets** — RAG
   evidence + server-resolved citations (5.e/5.f), surfaced in the Ask UI (5.g).
2. **A model cannot invent a clickable citation path** — server-owned citation
   mapping drops invented aliases (5.e); verified for both providers here.
3. **Local mode works with outbound access disabled and contacts no cloud** —
   Ollama adapter, `data_boundary=local` (5.b); search/Ask degrade to explicit
   errors, never egress.
4. **External mode sends only allowed question/evidence text** — policy gate +
   zero-metadata boundary counters (5.c), enforced in the orchestrator (5.f).
5. **An unavailable provider produces an explicit error, never a silent switch** —
   registry gate, no fallback (5.a); explicit Problem statuses (5.f).
6. **OpenAI and Ollama pass the same grounding/citation contract fixtures** — this
   step.

## 5. Follow-ups (beyond Phase 5)

- Wire the SSE keep-alive heartbeat (backend, noted in 5.f).
- Render the Markdown answer with a sanitizing renderer (frontend, noted in 5.g).
- Run the OpenAI live smoke in a gated pipeline when a key is available.
