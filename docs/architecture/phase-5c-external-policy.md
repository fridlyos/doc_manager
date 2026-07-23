# Phase 5.c — External-Processing Policy & Data-Boundary Accounting

**Status:** ✅ complete · **Branch:** `phase-5-generation` · **Spec:** TECHSTACK §5.13, §12; contract §8.1–8.2

The security gate that decides whether one Ask request may send evidence to an
external provider, plus the auditable counters that prove what crossed the
boundary. Default is **deny**, evaluated per request, **fail closed**, **no
fallback**. Wiring into `/ask` is Phase 5.f; this step delivers the decision
engine and the boundary accounting as pure, tested units.

---

## 1. Two gates, clearly separated

- **Deployment eligibility (5.a registry):** is this provider usable here at all —
  `external_llm_enabled` + on the allowlist + secret present? A miss →
  `provider_unavailable`. The Ask service checks this first.
- **Transfer policy (5.c, this step):** given an *eligible* external provider, may
  *this request's* evidence be transferred? This is the per-request, per-source
  gate.

Keeping them separate means "provider not enabled" and "this transfer is
forbidden" are distinct, correctly-typed outcomes.

## 2. Policy decision (`policy.py`)

`evaluate_external_policy(settings, provider, evidence_source_policies, acknowledged)
→ PolicyOutcome`:

| Situation | Decision |
| --- | --- |
| Provider is **local** | `allowed`, boundary `local` (policy N/A — no transfer) |
| External, `external_llm_enabled` false | `denied` (hard, fail-closed invariant) |
| External, **any** evidence source ≠ `allow` | `denied` (`denied_source_count > 0`) |
| External, all `allow`, **not acknowledged** | `confirmation_required` |
| External, all `allow`, acknowledged | `allowed` |

`PolicyOutcome` carries the `decision`, the `boundary`, a **source-name-free**
`reason`, and `denied_source_count`. Fail-closed: one denied source blocks the
whole request — evidence is never silently dropped and no other provider is
chosen (exit criterion 5). `evidence_source_policies` is the
`external_generation_policy` of every source location backing the *selected*
evidence (resolved by the 5.e/5.f Ask flow).

## 3. Data-boundary accounting (`boundary.py`)

`DataBoundaryReport` + `ExternalPayload` implement the contract §8.2
`data_boundary` object. The privacy invariant is **structural**: the metadata
counters — `paths_sent`, `file_names_sent`, `tags_sent`, `catalog_ids_sent`,
`original_files_sent` — have no code path that sets them; they are always `0`.
Only text/alias counters (question, grounding, evidence blocks/characters, opaque
citation ids) can be non-zero.

Builders:
- `local_boundary()` — classification `local`, everything false/zero.
- `external_boundary(acknowledged, attempted, occurred, …)` — classification
  `external`; once the HTTP write begins, `external_request_attempted` and the
  conservatively-named `external_transfer_occurred` are both true (even if the
  upstream later fails); if not attempted, the payload zeroes out.
- `confirmation_summary(provider_id, evidence_blocks, evidence_characters)` — the
  counts-only preview for an `external_confirmation_required` response; no provider
  call has occurred; metadata counts zero.

`external_policy_denied` (HTTP 403, non-retryable) was added to the generation
error taxonomy for the Ask route (5.f) to raise on a `denied` outcome.

## 4. Verification

10 unit tests (offline): local bypass; external-disabled denied; any-denied-source
fails closed with a count; all-allow-without-ack → confirmation-required;
all-allow-with-ack → allowed; empty-source-set allowed; and boundary accounting —
local report all-zero, external-attempted reports text counts with **zero
metadata**, not-attempted zeroes the payload, and the confirmation summary is
counts-only.

Gate: full backend suite **192 pass** (182 prior + 10 new); ruff + mypy clean.

## 5. Follow-ups

- **5.d** OpenAI adapter — its `secret_available` feeds the registry gate; the
  actual transfer happens only after a `PolicyOutcome.allowed`, and the adapter
  flips `attempted`/`occurred` in the boundary report.
- **5.f** `/ask` + `/ask/stream` call `evaluate_external_policy` after retrieval:
  `denied` → Problem `external_policy_denied` (403); `confirmation_required` →
  a safe `confirmation_summary` response with **no** provider call; `allowed` →
  generate and attach the `external_boundary` report.
