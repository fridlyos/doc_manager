# ADR 0006: Keep Sync Execution Out of the MVP Behind a Separately-Reviewed Executor Boundary

- **Status:** Proposed
- **Date:** 2026-08-06
- **Decision owners:** Project maintainers

## Context

Phase 7 compares source locations and produces **persisted, read-only dry-run sync
plans** (`sync_plans` / `sync_plan_items`): each plan classifies every source entry
as `already_present`, `copy` (missing in target), `conflict` (same path, different
bytes), or `manual_review` (renamed / text-equivalent). The plan proposes source and
target relative paths but contains **no execution columns** and there is **no
execute/apply API route**.

A natural follow-on is an *executor* that consumes a plan and performs the copies.
That capability is dangerous by nature — it writes to (and could overwrite or delete
in) user document roots that the rest of the system treats as read-only. The
project's stated invariants are: read-only sources, no silent egress, comparison and
planning only, and no automatic copy/move/delete in the MVP. An executor built
casually would violate all of them.

## Decision

**No sync execution ships in the MVP.** Sync planning is comparison + persisted
dry-run plans only. Any future executor is a **separate, independently reviewed
component**, disabled by default, and out of scope until it passes its own security
review. This ADR records the boundary a future executor MUST satisfy so that the
MVP's plan format can be treated as a stable, safe input to it later.

### The plan is an immutable input; the executor is a separate consumer

- Generated plans are **immutable** (a new comparison is a new plan). An executor
  reads a plan; it never mutates one.
- The executor lives in its own component/service, not in the API request path or
  the existing worker handlers, and is **absent from the default deployment**.

### Mandatory properties of any future executor (before it may exist)

1. **Disabled by default** — off unless an operator explicitly enables it, per
   deployment, after review. No env flag in the MVP turns it on because it does not
   exist.
2. **Allowlisted roots** — may only write under explicitly allowlisted target roots;
   never under a source root, and never outside the configured document trees.
3. **Explicit per-operation confirmation** — each proposed action (or a reviewed
   batch) requires explicit human confirmation; no blanket auto-apply of a plan.
4. **Conflict rules, never automatic overwrite** — `conflict` and `manual_review`
   items are never actioned automatically. Overwrite requires an explicit,
   per-item decision.
5. **No automatic delete** — deletion is out of scope entirely. The executor may
   propose/perform copies only; it never removes source or target files.
6. **Checksum-after-copy verification** — every copy is verified by re-hashing the
   written target and comparing to the planned source hash; a mismatch fails the
   operation and is recorded, not silently retried into corruption.
7. **Audit trail** — every attempted and completed operation is recorded (who,
   when, plan id, item id, source/target, result, checksum) in an append-only log.
8. **Re-validation at execution time** — the plan is a snapshot; the executor
   re-checks current hashes/paths before writing and refuses to act on stale state
   rather than trusting the plan blindly.
9. **Its own security review** — the executor requires a dedicated review covering
   path traversal, symlink handling, partial-failure recovery, and credential/mount
   exposure before it is merged or enabled.

### What the MVP guarantees today

- `POST /api/v1/sync-plans` builds a plan by comparing **catalog hashes only**; the
  handler opens no file, so building a plan cannot alter any source root (proven by
  an E2E test that fingerprints both roots before/after).
- There is intentionally **no** `/sync-plans/{id}/execute` or `/apply` route
  (asserted by test), no execution columns in the schema, and no copy/move/delete
  code path.

## Consequences

- **Positive:** the MVP cannot damage user files through synchronization; the plan
  format is a stable, reviewable contract a future executor can consume; the
  dangerous capability is quarantined behind an explicit, documented boundary.
- **Negative:** users must perform any actual file synchronization manually (or with
  their own tools) using the plan as guidance until a reviewed executor exists.
- **Follow-up:** if an executor is later pursued, it gets its own ADR (superseding
  the "no executor" posture only for that separate, reviewed component) and its own
  phase, honoring every property above.

## Alternatives considered

- **Ship a minimal executor now** — rejected: it puts write access to user document
  roots on the critical path without the review such a capability demands, breaking
  the read-only-sources invariant.
- **Add execution columns "for later"** — rejected: unused execution state invites
  accidental wiring and misleads readers about the MVP's guarantees; the schema
  stays comparison-only.
