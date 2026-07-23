# Phase 4.c — Qdrant Collection Lifecycle & Idempotent Point Operations

**Status:** ✅ complete · **Branch:** `phase-4-search` · **Spec:** TECHSTACK §5.9, §14 (Phase 4.c)

Documents the vector-store repository: collection lifecycle bound to an embedding
profile, deterministic idempotent point upserts, filtered search, deletion, and
the point accessor a consistency check needs — plus how it was verified against
both an in-memory client and a real Qdrant server.

---

## 1. Purpose and place in the pipeline

Embeddings (Phase 4.b) become searchable only once stored in a vector index.
Phase 4.c is the **Qdrant boundary**: one repository that owns a collection,
writes deterministic points, and answers nearest-neighbour queries.

```
chunk (4.a) → embed (4.b) → QdrantRepository.upsert_points (4.c) → search (4.d)
                            QdrantRepository.search        (4.c) ← query vector (4.b)
```

## 2. Module layout

```
backend/src/doc_manager/vectors/
├── __init__.py     public exports
├── errors.py       VectorStoreError + VectorStoreErrorCode (collection_mismatch)
├── point.py        point_id(), VectorPoint, build_point() (retrieval-only payload)
└── repository.py   QdrantRepository, SearchHit, build_qdrant_repository()
```

Tests: `backend/tests/unit/test_vectors.py` (11 tests, in-memory client).
Dependency added: `qdrant-client>=1.12,<1.14` (resolves to 1.13.3 — see §7).

## 3. Point identity and payload (`point.py`)

**Point ID** is `uuid5(_POINT_NAMESPACE,
"{content_object_id}:{chunking_profile}:{embedding_profile}:{chunk_index}")`. It
folds **both** profiles, resolving open decision #2: the chunk row ID (Phase 4.a)
is embedding-agnostic, but a vector point is specific to an embedding profile, so
the same chunk embedded under two models yields two non-colliding points. Same
content + same profiles + same ordinal ⇒ identical point ID ⇒ re-index overwrites
in place (Phase 4 exit criterion 1).

**Payload is retrieval-only** (TECHSTACK 5.9): `content_object_id`, `chunk_id`,
`chunk_index`, `page_start`, `page_end`, `text`, `chunking_profile_hash`,
`embedding_profile_hash`. It deliberately excludes paths, filenames, tags, and
source names — a test asserts the exact key set. Those are resolved from
PostgreSQL at query time so a moved or renamed file never yields a stale citation.

## 4. The repository (`repository.py`)

`QdrantRepository(client, collection=...)` wraps an `AsyncQdrantClient` for one
collection. The app/worker builds it via `build_qdrant_repository(settings,
profile)`, which names the collection `profile.collection_name(settings.qdrant_collection)`
— so each embedding profile gets its own collection.

### `ensure_collection(profile)` — lifecycle + refusal

- Absent → create with the profile's vector size and distance
  (`cosine`→`Distance.COSINE`).
- Present → read the existing geometry and validate it with
  `profile.is_compatible_with(vector_size, distance)`; a mismatch raises
  `VectorStoreError(collection_mismatch)` **instead of writing incompatible
  vectors** (TECHSTACK 5.8/5.9). Named-vector collections are also refused (this
  repo uses a single unnamed vector).

### `upsert_points(points)` — idempotent

Upserts by deterministic point ID, so re-running is an overwrite, not a
duplicate. Empty input is a no-op returning 0. Verified: a double upsert of the
same points leaves `count_for_content == 1`.

### `search(vector, *, top_k, score_threshold, content_object_ids)`

Nearest points via `query_points`, with an optional **content-object allow-set**.
Source/extension/tag/status filters are *not* in the payload; the retrieval layer
(Phase 4.d) resolves them from PostgreSQL to a set of allowed `content_object_id`s
and passes it here (resolving open decision #4: filter placement). An empty
allow-set short-circuits to `[]` with no query. Results come back as `SearchHit`
(id, score, and the payload fields).

### `delete_for_content(id)` / `count_for_content(id)` / `point_ids_for_content(id)`

Delete removes every point for a content object (unreferenced content or a retired
profile). `point_ids_for_content` scrolls the full point set for a content object
so the Phase 4.e consistency check can diff SQL chunk rows against vector points.

## 5. Public API

```python
repo = build_qdrant_repository(settings, profile)   # collection per profile
await repo.ensure_collection(profile)               # create or validate/refuse
await repo.upsert_points([build_point(...) …])      # idempotent
hits = await repo.search(qvec, top_k=12, score_threshold=0.4,
                         content_object_ids=allowed)  # allow-set from SQL
await repo.delete_for_content(content_object_id)
```

## 6. Verification

- **Unit (in-memory `AsyncQdrantClient(":memory:")`, 11 tests):** create when
  absent + idempotent re-validate; refuse size mismatch; refuse distance mismatch;
  idempotent upsert (double upsert → count 1); empty upsert no-op; ranked search
  with a score threshold dropping the orthogonal hit; content-object filter +
  empty allow-set short-circuit; delete removes points; `point_ids_for_content`
  equals the deterministically derived IDs; point ID folds content + both profiles
  + index; payload contains only the retrieval-only key set.
- **Real Qdrant server (manual smoke):** against the running dev container
  (server 1.12.4) — `ensure_collection`, double upsert → count 1, ranked search,
  size-mismatch refusal, and delete all confirmed.

Gate: full backend suite **144 pass** (133 prior + 11 new); ruff + mypy clean.

## 7. Client/server version alignment

`qdrant-client` initially resolved to 1.18.0, which logs an incompatibility
warning against the pinned server image `qdrant/qdrant:v1.12.4` (Qdrant requires
matching majors and a minor gap ≤ 1). Pinned to `>=1.12,<1.14` (resolves 1.13.3)
so the client tracks the server band and the warning disappears; all operations
re-verified on 1.13.3. Bumping the server image is a separate ops/ADR decision;
this keeps the client aligned with the current deployment.

## 8. Follow-ups

- **4.d** `/search`: embed the query (4.b), resolve SQL filters → allowed
  `content_object_id`s, call `repo.search`, then resolve paths/pages/state from
  PostgreSQL and build the result envelope. No generation provider (exit
  criterion 3).
- **4.e** consistency check job: diff `chunks` SQL rows vs `point_ids_for_content`
  per profile; report/repair drift.
- **Integration** (`index_file`): `ensure_collection` once, then
  `upsert_points(build_point(...))` for the document's chunks inside the fenced
  transaction; persist matching `chunks` rows (migration 0004, Phase 4.d).

## 9. Open decisions resolved

- #2 (point identity): point ID folds content object + chunking profile +
  embedding profile + chunk index.
- #4 (filter placement): source/extension/tag/status resolve in SQL to a
  content-object allow-set; Qdrant filters only on `content_object_id`. Payload
  stays free of paths/tags/source.
