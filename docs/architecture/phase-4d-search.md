# Phase 4.d — `/search`, chunk persistence, and the indexing integration

**Status:** ✅ complete · **Branch:** `phase-4-search` · **Spec:** TECHSTACK §5.9, §8, §14 (Phase 4.d)

This is the step that makes the corpus searchable end to end. It adds the `chunks`
SQL table, wires chunking + embedding + Qdrant upsert into the `index_file` job,
and exposes `POST /api/v1/search`. It documents the schema, the integration, the
retrieval flow, the security posture, and verification.

---

## 1. What shipped

```
scan → index_file:  extract → normalize → artifact                (Phase 3)
                             → chunk (4.a) → embed (4.b)
                             → ensure_collection + upsert (4.c)
                             → persist chunks rows                 ← NEW (4.d)
                             → mark indexed

POST /api/v1/search: embed query → SQL filter → Qdrant search      ← NEW (4.d)
                     → resolve current paths from PostgreSQL
```

## 2. `chunks` table + migration 0004

`chunks` is the SQL authority for the retrieval chunks of a content object under a
chunking profile. **Metadata only** — the chunk text lives in the Qdrant payload
and the extracted artifact, not in PostgreSQL — so a consistency check (Phase 4.e)
can diff SQL rows against vector points without duplicating text.

| Column | Purpose |
| --- | --- |
| `id` (PK) | Deterministic chunk id = `uuid5(content_object, chunking_profile, index)` |
| `content_object_id` (FK, cascade) | Owning content object |
| `chunk_index`, `page_start`, `page_end`, `token_count`, `text_hash` | Chunk metadata |
| `chunking_profile_hash` | Chunking profile that produced the row |
| `embedding_profile_hash` | Embedding profile whose points currently represent it |

Unique on `(content_object_id, chunking_profile_hash, chunk_index)` (redundant with
the deterministic PK, but documents the reuse key). Migration `0004` create/drop
verified with an upgrade → downgrade → re-upgrade round-trip against real
PostgreSQL.

## 3. Indexing integration (`index_file`)

After the artifact step, `handle_index_file`:

1. **Chunks** the normalized pages with the active chunking profile
   (`Settings.chunk_target_tokens`/`chunk_overlap_tokens`).
2. **Reuse check** (`_already_indexed`): if a content object with the full chunk
   set already exists for the active chunking+embedding profiles, skip embedding —
   a duplicate file reuses the existing chunks and points.
3. Otherwise **embeds** the chunk texts (off the event loop via `asyncio.to_thread`)
   and calls `ensure_collection`. Both happen **before** the fenced publish
   transaction so the expensive embed does not hold the job lock.
4. Inside the existing lease-fenced publish transaction (`_publish_success`):
   resolve/create the content object, then `_index_chunks` **upserts the vector
   points and the `chunks` rows** (both idempotent on the deterministic id — Qdrant
   by point id, SQL via `INSERT … ON CONFLICT DO UPDATE`), links the file version,
   and marks the entry `indexed`.

Because chunk/point ids are deterministic and keyed on the content object,
re-indexing an unchanged file writes zero new chunks or points (Phase 4 exit
criterion 1), and a duplicate file reuses one set. Points are written before the
transaction commits, so a committed `indexed` state always implies searchable
vectors.

## 4. `POST /api/v1/search`

Request (typed filter object, contract §5.3; bounded by server policy):

```json
{
  "query": "when does the acme agreement renew?",
  "filters": { "source_location_ids": ["…"], "extensions": ["pdf"], "document_ids": ["…"] },
  "retrieval": { "top_k": 12, "score_threshold": 0.4 }
}
```

- `query` required, non-blank, ≤ 2000 chars. Empty/blank → `validation_failed`.
- Filter arrays must be non-empty when present (`min_length=1`); unknown fields
  rejected (`extra="forbid"`).
- `top_k` defaults to `Settings.search_top_k`, capped at 100; `score_threshold`
  defaults to `Settings.search_score_threshold`.

Flow (`RetrievalService.search`):

1. If any filter is set, resolve the **allowed content-object ids** in SQL
   (catalog entries whose *current* file version satisfies every filter). An empty
   allow-set short-circuits to no results — no vector query.
2. Embed the query (`embed_query`, model query prefix) and search Qdrant with the
   score threshold and the allow-set.
3. Resolve each hit's **current display paths + availability from PostgreSQL** (not
   the vector payload), so a moved/renamed file yields a fresh citation. Primary
   path first; `availability` is `current` (indexed), `missing`, or `historical`
   (no current entry).

Response `data`: `results[]` (`chunk_id`, `similarity_score`, `page_start/end`,
`snippet`, `availability`, `paths[]` with `display_path`/`state`/`is_primary`),
`result_count`, `top_k`. **No generation provider is invoked** — search stays
available with generation disabled (exit criterion 3).

## 5. Security posture

- Qdrant payload is retrieval-only; paths, filenames, tags, and source names are
  resolved from PostgreSQL at query time (TECHSTACK 5.9). A test asserts the exact
  payload key set.
- Responses expose only `display_path` (never `scan_root`), via the shared
  `core.display.display_path` helper used by both the document serializer and
  retrieval.
- No route accepts a filesystem path; filters are resource ids and extensions.

## 6. Verification

- **Unit — endpoint (`test_api_search.py`, 9 tests):** envelope shape + serialized
  fields; default vs explicit `top_k`/threshold forwarding; filter forwarding;
  blank/empty query, empty filter array, `top_k` bounds, and unknown-field
  rejection; asserts no `provider`/`answer` fields leak. Fake service injected —
  no PG/Qdrant/model.
- **Unit — chunking/embedding/vectors:** covered in 4.a/4.b/4.c.
- **Integration — indexing (`test_index_file.py`, +2):** chunk rows + one point per
  chunk are persisted; **re-index creates no duplicate chunks or points**
  (exit criterion 1); duplicate file reuses one chunk set.
- **Integration — retrieval (`test_retrieval.py`, 4, real PG + in-memory Qdrant):**
  a **golden query retrieves the expected document** (exit criterion 2); extension
  filter constrains candidates; empty filter-set short-circuits; a **moved file
  resolves its current path** from PostgreSQL.
- **Real-stack smoke (manual):** real `bge-small-en-v1.5` + real Qdrant through the
  repository — the renewal document ranks first (0.675 vs 0.388) and the threshold
  filters the distractor.

Gate: full backend suite **159 pass** (144 prior + 15 new); ruff + mypy clean;
migration 0004 round-trips.

## 7. Follow-ups

- **4.e:** search UI + the `catalog_consistency_check` job (diff `chunks` rows vs
  `point_ids_for_content` per profile). The `chunks` table and the repository's
  `point_ids_for_content` accessor are already in place for it.
- **Known limitation (from 4.b):** long chunks may exceed bge-small's 512-token
  input and be truncated for embedding; the full chunk text is still stored for
  citations. Aligning the chunk target to the model's token budget is tracked
  against open decision #1.
