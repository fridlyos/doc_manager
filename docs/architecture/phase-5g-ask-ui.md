# Phase 5.g — Ask UI

**Status:** ✅ complete · **Branch:** `phase-5-generation` · **Spec:** TECHSTACK §5.13; contract §8

The operator-facing Ask screen over the Phase 5.f endpoints: provider selection, a
persistent Local/External data-boundary badge, a streamed answer with evidence
cards, and an external-data preview/confirmation gate.

---

## 1. Client (`client.ts`)

- `fetchProviders()` → `GET /system/providers` (id, data_boundary, eligible).
- `askStream(body, onEvent, signal)` — POSTs to `/ask/stream` and consumes the SSE
  response with `fetch` + a `ReadableStream` reader (browser `EventSource` cannot
  send the required JSON body). Frames are split on the blank line and parsed into
  `{ event, data }`; comments/keep-alives are ignored. On a non-2xx pre-stream
  response it throws the Problem `detail`.
- Types mirror the §8.2 result: `AskResultData`, `AskCitation`, `ProviderInfo`.

## 2. AskPage (`pages/AskPage.tsx`, route `/ask`, in nav)

- **Provider select** populated from eligible providers; a **persistent
  Local/External badge** reflects the selected provider's `data_boundary`
  (colour-coded green/amber).
- **Submit** streams the answer: `answer.delta` events append progressively; the
  terminal `ask.result` **reconciles** to the authoritative answer, and
  `ask.error` surfaces the Problem detail.
- **Evidence cards** — each citation shows `[ordinal]`, the primary `display_path`,
  a page label, an availability badge, and the snippet. Answer markers already read
  `[1]` (server-rewritten).
- **External confirmation gate** — when the result is
  `external_confirmation_required`, a preview shows the counts-only summary
  (evidence blocks/characters, provider) and states that no paths/file names/tags
  are sent; a **"Send to external provider"** button re-runs the request with
  `external_processing_acknowledged: true`.
- **States** — insufficient-evidence and refusal render explicit notices; warnings
  (e.g. a dropped invented citation) are shown.

## 3. Security posture (client side)

- Only server-resolved `display_path`s are shown; the UI never constructs a query
  from a filesystem path.
- The external boundary is explicit: the badge is always visible, and an external
  send requires the confirmation click — the acknowledgement flows to the server,
  which still enforces the policy.

## 4. Verification

4 Vitest tests (fetch + a hand-built `ReadableStream` for the SSE, robust across
test environments): providers load and the **Local badge** shows; **streamed
deltas render then reconcile** with the final answer + citation card; **external
confirmation** prompts and the **resend carries `acknowledged: true`**; an
`ask.error` renders. Full frontend suite **19 pass**; eslint + tsc clean;
production build succeeds.

## 5. Follow-ups

- **5.h** — shared provider contract fixtures (Ollama + OpenAI through the same
  grounding/citation assertions) and an optional OpenAI live smoke.
- Minor: render the Markdown answer with a sanitizing renderer (currently shown as
  pre-wrapped text — safe, but not formatted); wire the SSE keep-alive heartbeat
  (backend follow-up from 5.f).
