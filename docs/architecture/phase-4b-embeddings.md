# Phase 4.b — FastEmbed Adapter & Embedding-Profile Validation

**Status:** ✅ complete · **Branch:** `phase-4-search` · **Spec:** TECHSTACK §5.8, §14 (Phase 4.b)

Documents the local embedding layer: what it does, the profile identity that keeps
incompatible vectors apart, the API, and how it was verified against the real
model.

---

## 1. Purpose and place in the pipeline

Chunks (Phase 4.a) become searchable only once turned into vectors. Phase 4.b is
the **embedding boundary**: a thin, testable adapter over
[FastEmbed](https://github.com/qdrant/fastembed) (local ONNX models, CPU, no
external API) plus the *embedding profile* that names the vector space.

```
chunk_pages (4.a) → EmbeddingService.embed_documents (4.b) → Qdrant upsert (4.c)
                    EmbeddingService.embed_query      (4.b) → /search (4.d)
```

Embeddings are computed locally; nothing leaves the host. This is what lets search
run with **no generation provider** configured (Phase 4 exit criterion 3) — the
generation boundary is Phase 5.

## 2. Module layout

```
backend/src/doc_manager/embedding/
├── __init__.py    public exports
├── errors.py      EmbeddingError + EmbeddingErrorCode
├── profile.py     EmbeddingProfile, hash, collection name, compatibility check
└── service.py     Embedder protocol, EmbeddingService, FastEmbed loaders
```

Tests: `backend/tests/unit/test_embedding.py` (11 unit tests).
Dependency added: `fastembed>=0.8.0` (pulls `onnxruntime`, `tokenizers`, `numpy`).

## 3. Embedding profile and identity (`profile.py`)

Two vectors are comparable only if they come from the same model, size, metric,
normalization, and prefix scheme. `EmbeddingProfile` captures exactly that:

| Field | Meaning |
| --- | --- |
| `model_name` | FastEmbed model id (default `BAAI/bge-small-en-v1.5`) |
| `vector_size` | Output dimensionality (bge-small = 384) |
| `distance` | Similarity metric — `cosine` for normalized embeddings |
| `normalize` | Whether outputs are unit-normalized (bge → yes) |
| `prefix_scheme` | `"fastembed"` = library applies model query/passage prompts |
| `version` | `EMBEDDING_PROFILE_VERSION` (`"embed-1"`) |

- `profile.hash` — SHA-256 of the canonical JSON of those fields.
- `profile.collection_name(base)` → `"{base}__{model-slug}__{hash[:12]}"`. Binding
  the collection name to the profile hash is the mechanism behind TECHSTACK 5.8:
  **an embedding-profile change routes to a new collection, never silently mixing
  incompatible vectors.** The slug is human-readable; the hash guarantees
  uniqueness.
- `profile.is_compatible_with(vector_size, distance)` — used by the Phase 4.c
  Qdrant repository to *validate and refuse* an existing collection whose geometry
  disagrees with the active profile, rather than corrupt it.

`profile.py` is pure and dependency-free; the FastEmbed lookups that populate a
profile live in `service.py`.

## 4. The service (`service.py`)

### Embedder protocol

`EmbeddingService` depends on a tiny structural interface, not FastEmbed directly:

```python
class Embedder(Protocol):
    def passage_embed(self, texts, **kwargs) -> Iterable[Any]: ...
    def query_embed(self, query, **kwargs) -> Iterable[Any]: ...
```

FastEmbed's `TextEmbedding` satisfies it. Tests inject a fake, so the suite never
loads a model or hits the network.

### Separate document and query paths

`embed_documents` calls `passage_embed`; `embed_query` calls `query_embed`. Using
FastEmbed's two entry points means the **model's own query/passage prompts are
applied correctly** (TECHSTACK 5.8) — for bge, the query gets the retrieval
instruction prefix and passages do not. The service never hand-rolls prefixes, so
prompt correctness tracks the library/model.

### Validation (fail closed)

Every returned vector is checked against `profile.vector_size`, and the document
path checks vector count equals input count. A mismatch raises
`EmbeddingError(vector_size_mismatch)` — a wrong model or corrupted output fails
loudly instead of writing junk into the collection. Unknown model / load failure
raise `unknown_model` / `model_load_failed`. These are **permanent** errors (the
`index_file` job will map them to a permanent job failure, not an endless retry).

### One load per process

`load_fastembed(model_name)` is `lru_cache`d, so a worker downloads/loads the ONNX
model once and reuses it (TECHSTACK 5.8). FastEmbed is imported **lazily** inside
the loader, so importing the module — and running the unit tests — never pays the
heavy `onnxruntime` import or triggers a download.

### Wiring

`build_embedding_service(settings)` resolves the profile (reading the model's
vector size from FastEmbed's registry via `get_embedding_size`, no download),
loads the cached model, and returns an `EmbeddingService` batched at
`settings.embedding_batch_size` (default 256).

## 5. Public API

```python
svc = build_embedding_service(settings)          # process-cached model
doc_vectors = svc.embed_documents([chunk.text …]) # list[list[float]], order kept
query_vector = svc.embed_query("when does it renew?")
svc.vector_size          # 384
svc.profile.collection_name(settings.qdrant_collection)
```

## 6. Verification

- **Unit (offline, fake embedder):** passage path preserves order and never uses
  the query prefix; query path uses `query_embed`; empty input → empty output;
  vector-size mismatch rejected; batch size forwarded; profile hash + collection
  name stable and sensitive to every field; `is_compatible_with`; invalid-profile
  and unknown-model guards. The registry test resolves bge-small → 384 without a
  download (skips if fastembed is absent).
- **End-to-end (real model, manual — not a CI test):** loaded
  `BAAI/bge-small-en-v1.5`, embedded two passages + a query. Output is 384-d and
  unit-normalized (‖v‖ = 1.0). Cosine similarity ranked the relevant passage above
  the distractor (0.73 vs 0.45), confirming the prefix paths and vector space work
  as intended. The real-model embed is intentionally excluded from the unit suite
  (≈130 MB download); Phase 4.c integration tests will cover a real embed behind a
  reachability guard.

Gate: full backend suite **133 pass** (122 prior + 11 new); ruff + mypy clean.

## 7. Follow-ups

- **4.c** creates/validates the Qdrant collection named by
  `profile.collection_name(...)`, refusing a geometry mismatch via
  `is_compatible_with`, and upserts points (embedding profile folded into the
  point id).
- **Integration** loads the service once per worker and embeds `Chunk.text` inside
  `index_file` after chunking.
- **Known limitation:** bge-small truncates inputs beyond 512 wordpiece tokens.
  With the whitespace tokenizer's default 750-token target a long chunk can exceed
  that and be truncated for embedding (the full chunk text is still stored for
  citations). Aligning the chunking target to the model's token limit — or adopting
  the model tokenizer as a chunking profile — is a candidate refinement tracked
  against open decision #1.

## 8. Open decisions touched

- #3 (collection-per-profile naming): **resolved** — single active collection named
  `{qdrant_collection}__{model-slug}__{profile-hash[:12]}`.
- #2 (point identity): the embedding profile hash is now available to fold into the
  Phase 4.c point id alongside the chunk id.
