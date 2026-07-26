# Phase 6.b — Profile-Driven Rebuild & Stale-Vector Retirement

**Status:** ✅ complete · **Branch:** `phase-6-reindex-duplicates` · **Spec:** TECHSTACK §5.9, §14 (Phase 6.b)

A model/profile change produces an **explicit, controlled rebuild** (exit criterion
3): re-index the corpus under the new profile, then retire the collection the old
embedding profile left behind. New-before-old, same-namespace only.

---

## 1. The controlled rebuild (two steps)

A profile change is an operator action (profiles are config/secrets), so the
rebuild is explicit and sequenced — no fragile auto-chaining across a job engine
without dependencies:

1. **Rebuild** — `POST /api/v1/system/reindex` (Phase 6.a) re-indexes every
   document under the **current** profiles. Because embedding-profile identity
   names the Qdrant collection (Phase 4.c), a new embedding profile writes a
   **new** collection while the old one still holds the previous points — search
   stays consistent throughout.
2. **Retire** — `POST /api/v1/system/remove-stale-vectors`, run **after** the
   rebuild completes, drops the superseded collection(s).

The new collection exists before the old is dropped — a controlled rebuild with no
mixed/incompatible vectors in the active collection.

## 2. `remove_stale_vectors` handler (`jobs/handlers/cleanup.py`)

Registered for `JobType.remove_stale_vectors`. It resolves the **active** embedding
profile (a FastEmbed registry lookup — no model load), then drops every Qdrant
collection that is in **our namespace** (`{qdrant_collection}__…`) but is **not**
the active one. The core is the pure-ish `drop_stale_collections(repo, *, active,
base_prefix)` helper. Unrelated collections are never touched; re-running is a
no-op.

Two new `QdrantRepository` primitives back it: `list_collection_names()` and
`drop_collection(name)`.

**Chunk rows self-heal**, so this job only retires *vector collections*, not SQL:
`chunk_id = uuid5(content_object, chunking_profile, index)` is embedding-agnostic,
so an embedding-profile rebuild upserts each chunk row **in place** with the new
`embedding_profile_hash`. Orphan content and chunking-profile-superseded rows are
Phase 6.d.

## 3. Endpoint

`POST /api/v1/system/remove-stale-vectors` — Idempotency-Key, `202`,
`Location: /jobs/{id}`, idempotent replay, deduped on `remove_stale_vectors`. Heavy
work runs in the worker.

## 4. Verification

- **Unit (in-memory Qdrant), 2 tests:** `drop_stale_collections` drops only the
  stale same-namespace collection, keeps the active one, and **never touches an
  unrelated collection**; no-stale is a no-op.
- **Endpoint (PG, TestClient), 2 tests:** `202` durable `remove_stale_vectors` job +
  idempotent replay; missing Idempotency-Key → `400`.

Gate: full backend suite **255 pass, 1 skipped**; ruff + mypy clean.

## 5. Follow-ups

- **6.d** extends `remove_stale_vectors` (or a sibling) with **orphan cleanup**:
  content objects with no active file_version, and chunk rows/points superseded by
  a chunking-profile change — the delete-convergence half of exit criterion 1.
- The rebuild sequence could later be chained (a completion-gated cleanup) if the
  job engine gains dependencies; for now the two-step operator flow is explicit and
  robust.
