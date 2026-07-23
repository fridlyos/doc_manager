# Phase 4 Progress Status

**Branch:** `phase-4-search` (cut from `phase-3-scanner-extraction` @ `226520a`).

> Cut from the Phase 3 branch, not `main`, because Phase 4 extends the Phase 3
> `index_file` pipeline and those commits are not yet merged to `main`.

Scope = TECHSTACK §14 "Phase 4: Chunking, Embeddings, and Vector Search".

## Deliverables

| # | Deliverable | Status |
| --- | --- | --- |
| 4.a | Deterministic page-aware chunking | **✅ complete** |
| 4.b | FastEmbed adapter + embedding-profile validation | **✅ complete** |
| 4.c | Qdrant collection lifecycle + idempotent point operations | **✅ complete** |
| 4.d | `/search` with filters, thresholds, snippets, pages, current paths | **✅ complete** |
| 4.e | Search UI + vector/catalog consistency check | **✅ complete** |
| — | **Integration: extend `index_file` with chunk → embed → upsert** | **✅ complete** |

**Phase 4 is complete.** All deliverables and exit criteria are met.

## Exit criteria (whole phase)

- Repeated indexing creates no duplicate chunks or vector points — deterministic
  chunk/point IDs derived from content + profile identity make re-index an idempotent upsert.
- A known query retrieves expected synthetic evidence — golden-query test over a synthetic corpus.
- Search remains functional without any generation provider — `/search` never touches a
  generation provider (that boundary is Phase 5); readiness `search_only` already models this.

## Design constraints carried from earlier phases

- **Content-addressed reuse (Phase 3).** A `content_object` already dedupes text-equivalent,
  same-pagination files. Chunks and vector points are keyed off the **content object**, not the
  catalog entry, so N duplicate files share one set of chunks/points. Paths are resolved from
  PostgreSQL at query time (never stored in Qdrant payload) so a move never staleness-breaks a
  citation (TECHSTACK 5.9).
- **Profile isolation (TECHSTACK 5.8).** An embedding-profile change must not write incompatible
  vectors into the active collection; it creates a new collection / controlled rebuild. The
  collection name binds the embedding profile identity.
- **Read artifacts, not sources (Phase 3).** The chunker consumes `ArtifactStore.load_pages`
  (page/section boundaries preserved at normalization) — it never reopens the original file.
- **Lease-fenced publication (Phase 2/3).** The upsert + chunk-row publication happens in the
  `index_file` final fenced transaction, consistent with existing handlers.

---

## Completed work

### 4.a — Deterministic page-aware chunker ✅ (2026-07-22)

Delivered `doc_manager/chunking/` (pure library — no DB/vectors/FS):
`tokenizer.py` (`Tokenizer` protocol + pure `WhitespaceTokenizer`, lossless on
normalized text), `profile.py` (`ChunkingProfile` + `chunking_profile_hash`,
`CHUNKING_VERSION="chunk-1"`, deterministic `chunk_id` via UUIDv5 over
content-object + profile + index), and `chunker.py` (`Chunk` record +
`chunk_pages`). Algorithm: page boundaries are preferred cut points — small pages
are packed up to `target_tokens`, oversized pages are split into overlapping
windows that never cross a page. Deterministic IDs make re-index an idempotent
upsert (exit criterion 1). 13 unit tests; full backend suite **122 pass**;
ruff/mypy clean. **Full report: `docs/architecture/phase-4a-chunking.md`.**

Resolves open decision #1 (tokenizer source): pluggable, pure whitespace default
now; model-accurate tokenizer becomes a distinct profile in 4.b.

### 4.b — FastEmbed adapter + embedding-profile validation ✅ (2026-07-22)

Delivered `doc_manager/embedding/` (dep `fastembed>=0.8.0`): `profile.py`
(`EmbeddingProfile` — model/vector_size/distance/normalize/prefix_scheme/version;
`embedding_profile_hash`, `collection_name` = `{base}__{model-slug}__{hash[:12]}`,
`is_compatible_with` for 4.c collection validation), `service.py` (`Embedder`
protocol, `EmbeddingService` with separate `embed_documents`/`embed_query` prefix
paths, per-vector size validation, `lru_cache`d model load, lazy FastEmbed import),
`errors.py`. Config adds `embedding_batch_size`. 11 unit tests (offline via a fake
embedder; one real-registry dim lookup); full backend suite **133 pass**;
ruff/mypy clean. Verified end-to-end against real bge-small-en-v1.5: 384-d
normalized vectors, relevant passage outranks distractor (0.73 vs 0.45).
**Full report: `docs/architecture/phase-4b-embeddings.md`.**

Resolves open decision #3 (collection-per-profile naming). Makes the embedding
profile hash available for the 4.c point identity (open decision #2).

### 4.c — Qdrant repository: collection lifecycle + idempotent points ✅ (2026-07-22)

Delivered `doc_manager/vectors/` (dep `qdrant-client>=1.12,<1.14` → 1.13.3):
`point.py` (`point_id` folding content object + chunking + embedding profile +
index; `build_point` with retrieval-only payload — no paths/tags/source),
`repository.py` (`QdrantRepository` over `AsyncQdrantClient`: `ensure_collection`
create-or-validate/refuse via `profile.is_compatible_with`, idempotent
`upsert_points`, `search` with score threshold + content-object allow-set,
`delete_for_content`, `point_ids_for_content` for consistency checks;
`build_qdrant_repository` names the collection per profile), `errors.py`. 11 unit
tests via in-memory client; full backend suite **144 pass**; ruff/mypy clean.
Also smoke-verified against the real Qdrant dev server (idempotent upsert,
mismatch refusal, delete). Pinned the client to the server's version band to drop
the 1.18-vs-1.12.4 incompatibility warning. **Full report:
`docs/architecture/phase-4c-qdrant.md`.**

Resolves open decisions #2 (point identity) and #4 (filter placement: SQL →
content-object allow-set; Qdrant filters only on `content_object_id`).

### 4.d — `/search`, chunk persistence, indexing integration ✅ (2026-07-22)

Delivered: `chunks` SQL table + migration `0004` (metadata only; deterministic PK;
round-trip verified); `index_file` now chunks → embeds → `ensure_collection` →
upserts points → persists chunk rows inside the fenced publish transaction, with a
reuse check so a duplicate file skips re-embedding; `retrieval/` (`RetrievalService`
— SQL filter → content-object allow-set, query embed, Qdrant search, path/state
resolution from PostgreSQL) + shared `core.display.display_path`; `POST
/api/v1/search` (typed filters, bounded `top_k`/threshold, result envelope, **no
generation provider**) + `serialize_search_result`. 15 new tests (9 endpoint unit,
2 indexing incl. **no-duplicate re-index**, 4 retrieval incl. **golden query** and
**moved-file path resolution**); full backend suite **159 pass**; ruff/mypy clean.
Real-stack smoke (real bge-small + real Qdrant): renewal doc ranks first
(0.675 vs 0.388). **Full report: `docs/architecture/phase-4d-search.md`.**

Exit criteria met: (1) re-index creates no duplicate chunks/points; (2) a golden
query retrieves the expected synthetic evidence; (3) search runs with no generation
provider. Only 4.e (search UI + consistency check) remains in Phase 4.

### 4.e — Search UI + vector/catalog consistency check ✅ (2026-07-23)

Delivered: `catalog_consistency_check` job (`jobs/handlers/consistency.py`,
registered in `HANDLERS`) diffing SQL `chunks` vs Qdrant points per embedding
profile — `ConsistencyReport` (missing/orphan/drift), report-only, no model load
(`resolve_embedding_profile`); repository `point_ids_for_content` now filters by
embedding profile and guards a missing collection. Frontend `SearchPage` (`/search`,
nav) over `POST /search`: query + location + extension filters, hit cards with
display_path, page label, availability badge, score, and snippet; `client.ts`
`search()`. Backend **163 pass** (4 consistency tests); frontend **15 pass** (2
search tests); ruff/mypy/eslint/tsc clean; frontend build succeeds.
**Full report: `docs/architecture/phase-4e-search-ui-consistency.md`.**

**Phase 4 complete** — all deliverables + integration done; all three exit criteria met.

---

## Planned work

### 4.a — Deterministic page-aware chunker (pure library)

New `doc_manager/chunking/` (pure, no DB/vectors — mirrors `extraction/`):
- `tokenizer.py` — a single deterministic token counter shared by chunker and embedder so
  "tokens" means one thing. Start with the FastEmbed model's own tokenizer (bge-small = BERT
  wordpiece, 512-token model max) to avoid over-long chunks; fall back to a fixed heuristic only
  if loading the tokenizer at chunk time is undesirable. **Open decision — see below.**
- `chunker.py` — `chunk(NormalizedDocument, profile) -> list[Chunk]`:
  - target 750 tokens, 100 overlap, both from `Settings.chunk_target_tokens/overlap_tokens`.
  - packs whole pages/sections; **does not cross a page boundary** unless a single page exceeds
    the target, in which case it splits within the page and records the page range.
  - each `Chunk` carries: `index`, `token_count`, `text`, `page_start`, `page_end`, `section`,
    `text_hash` (NFC + collapsed ws, like normalize), and the chunking profile version.
  - deterministic `chunk_id = uuidv5(content_object_id + chunking_profile + chunk_index)` and a
    matching Qdrant point ID from the same identity, so re-runs are stable upserts (exit crit 1).
- `profile.py` — `chunking_profile_hash(version, target, overlap, tokenizer_id)`; a knob change
  yields a new profile so old chunks are never silently mixed.
- `CHUNKING_VERSION` constant, bumped on algorithm change.

Tests (unit): determinism (same input → identical ids/hashes), page-boundary respect,
oversized-page split, overlap correctness, token accounting, profile-hash sensitivity.

### 4.b — FastEmbed adapter + embedding-profile validation

New `doc_manager/embedding/`:
- add deps `fastembed` and `qdrant-client` to `backend/pyproject.toml` (with a mypy override if
  untyped, as done for pymupdf).
- `service.py` — `EmbeddingService`: loads the configured FastEmbed model **once per process**
  (module/worker singleton), exposes `embed_documents(texts)` and `embed_query(text)` as separate
  calls so model prefixes differ correctly (bge query prefix). Batches within a configurable size.
- `profile.py` — `EmbeddingProfile` capturing model name, revision, vector size, distance metric
  (cosine), and preprocessing/prefix profile; `embedding_profile_hash(...)` and a human-readable
  collection name derived from it. Refuses to mix incompatible vectors.
- Config: reuse `embedding_model`; add `embedding_batch_size`, and optionally
  `embedding_query_prefix`/`embedding_doc_prefix` (or derive from a model registry).

Tests (unit, model mocked/or tiny real model behind a marker): query vs document path differ;
vector size recorded; profile hash changes when model/metric changes; batching preserves order.

### 4.c — Qdrant repository: collection lifecycle + idempotent points

New `doc_manager/vectors/`:
- `repository.py` — `QdrantRepository`:
  - `ensure_collection(profile)` — create-if-absent with the profile's vector size + cosine
    distance; **validate** an existing collection matches the profile (size/metric) and refuse on
    mismatch rather than corrupt it.
  - `upsert_chunks(content_object_id, points)` — idempotent upsert by deterministic point id;
    payload is retrieval-only: `content_object_id`, `chunk_id`, `page_start`, `page_end`,
    `text`, and profile identifiers. **No paths, filenames, tags, or source names in payload.**
  - `delete_for_content(content_object_id)` / tombstone when canonical content is unreferenced or
    a profile is retired.
  - `search(vector, filters, top_k, score_threshold)` applying source/extension/tag/status
    filters (payload-side where possible, else resolved via SQL post-filter on content ids).
  - `consistency(profile)` — compare SQL `chunk` rows against Qdrant points; report missing/orphan.
- Health: `_check_qdrant` already exists; extend readiness/collection preflight if needed.

Tests (integration, PG + Qdrant): collection created once; re-upsert same content is a no-op
count-wise (exit crit 1); mismatched profile refused; delete removes points.

### 4.d — `/search` endpoint

- New model `chunks` (SQL) — the authority the consistency check compares against:
  `id (chunk_id)`, `content_object_id` FK, `chunk_index`, `page_start/end`, `section`,
  `token_count`, `text_hash`, `chunking_profile_hash`, `embedding_profile_hash`, `created_at`;
  unique on `(content_object_id, chunking_profile_hash, chunk_index)`. **Migration 0004.**
- `POST /api/v1/search` (contract §5.3 typed filter object; §8 result shape minus generation):
  request `{ query, filters{source_location_ids, extensions, tags, tag_mode, document_ids},
  retrieval{top_k, score_threshold} }`; bounded by server policy (`search_top_k`,
  `search_score_threshold`).
  - embed the query → Qdrant search → resolve each hit's paths/state from PostgreSQL at query
    time → build snippet + page range + `similarity_score` + `availability`
    (`current`/`missing`/`historical`). Never returns `scan_root`; only `display_path`.
  - returns the standard collection/result envelope; **invokes no generation provider** (exit
    crit 3). Empty/weak evidence returns an explicit empty/insufficient result, not an error.
- Serializer `serialize_search_hit` (paths resolved like `serialize_document.display_path`).

Tests (integration): golden synthetic query returns the expected chunk/evidence (exit crit 2);
filters (source/extension/state) constrain hits; threshold drops low scores; a moved file still
resolves a current path; search works with generation providers disabled.

### 4.e — Search UI + consistency check

- Frontend `SearchPage`: query box + filter controls (source location, extension, state), results
  list with snippet, page range, score, `display_path`, and availability badge. `client.ts`
  `search(body)` type. Nav + route `/search`. Vitest coverage like Documents/Errors pages.
- `catalog_consistency_check` job handler (already an enum member, TECHSTACK 5.9/7): compares SQL
  chunk rows ↔ Qdrant points for a profile, reports drift, and can enqueue re-index/cleanup.
  Optionally a `GET`/status surface; at minimum a durable job + structured log.

### Integration — extend `index_file`

After the artifact step in `jobs/handlers/index_file.py::_publish_success` (its current terminal
`indexed` state is unchanged):
1. chunk the `NormalizedDocument` (4.a),
2. embed document chunks (4.b),
3. `ensure_collection` + idempotent `upsert_chunks` keyed on the content object (4.c),
4. persist/refresh `chunks` rows in the same fenced transaction,
5. only then mark the entry `indexed`.

Because chunks/points key off the **content object**, a reused content object (duplicate file)
skips re-embedding — and a genuine re-index is an idempotent upsert, so no duplicate points
(exit crit 1). A reused-artifact path must still ensure chunk rows + points exist for that content
object (first file that produced it) but never double-writes.

New job types to wire (already in the `JobType` enum): `remove_stale_vectors`,
`reindex_document`, `reindex_all_for_profile`, `catalog_consistency_check`.

---

## Open decisions (resolve before/with 4.a–4.b)

1. **Tokenizer source.** Use the FastEmbed model's own tokenizer for chunk sizing (accurate,
   couples chunker to the embedding model and its 512-token max) vs. a standalone tokenizer
   (decoupled, risk of over-long chunks for the model). Leaning: model tokenizer, with the target
   clamped under the model max. *Affects 4.a determinism profile.*
2. **`content_object_id` on `chunks` vs. Qdrant point identity.** Confirm point id =
   `uuidv5(content_object_id, chunking_profile, embedding_profile, chunk_index)` so a chunking OR
   embedding profile change yields distinct points and never collides across profiles.
3. **Collection-per-profile naming.** Single active collection named from the embedding profile
   hash vs. the static `qdrant_collection` default. Plan uses a profile-derived name; keep
   `qdrant_collection` as a prefix/base.
4. **Filter application.** Which filters run as Qdrant payload filters vs. SQL post-filters
   (payload holds no source/tag/state, so those resolve via content-object → catalog-entry SQL).

## New dependencies

- `fastembed` — local embeddings (ONNX; CPU).
- `qdrant-client` — vector store client (has async support).

## Ops note

The dev stack needs Qdrant reachable (`qdrant` service in `compose.yaml`, already present) and the
embedding model downloaded on first worker start (cache the model dir on a volume to avoid repeat
downloads). Integration tests need both PostgreSQL **and** Qdrant on `127.0.0.1` (mirror the
existing `pg_url` skip-guard with a `qdrant_url` reachability guard).
