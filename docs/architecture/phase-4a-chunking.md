# Phase 4.a — Deterministic Page-Aware Chunking

**Status:** ✅ complete · **Branch:** `phase-4-search` · **Spec:** TECHSTACK §5.7, §14 (Phase 4.a)

This report documents the chunking library delivered for Phase 4.a: what it does,
why it is shaped this way, the exact algorithm, the public API, the identity and
determinism guarantees it provides to the rest of Phase 4, and its test coverage.

---

## 1. Purpose and place in the pipeline

Search retrieves *chunks*, not whole documents: a chunk is a bounded span of a
document's extracted text, small enough that several fit inside a generation
model's context window, large enough to carry a coherent idea. Phase 4.a produces
those chunks from the normalized page records that Phase 3 already stores.

Pipeline position:

```
extract (3.b) → normalize (3.c) → [artifact store]           ← Phase 3
                          │
                          ▼
                    chunk_pages (4.a)  ← THIS STEP: pure, no DB / vectors / FS
                          │
                          ▼
             embed (4.b) → Qdrant upsert (4.c) → /search (4.d)  ← later Phase 4
```

Like the extraction and normalization libraries, chunking is a **pure library**:
no database, no vector store, no filesystem access. It transforms in-memory
`NormalizedPage` records into in-memory `Chunk` records. The `index_file` job will
drive it in the Phase 4 integration step, reading pages from the artifact store
(`ArtifactStore.load_pages`) so the original source file is never reopened.

## 2. Module layout

```
backend/src/doc_manager/chunking/
├── __init__.py     public exports
├── tokenizer.py    Tokenizer protocol + WhitespaceTokenizer (pure default)
├── profile.py      ChunkingProfile, profile hash, CHUNKING_VERSION, chunk_id()
└── chunker.py      Chunk record + chunk_pages() algorithm
```

Tests: `backend/tests/unit/test_chunker.py` (13 unit tests).

## 3. Tokenizer (`tokenizer.py`)

The chunker measures size in *tokens*, so "token" must mean one fixed thing per
chunking profile. `Tokenizer` is a small protocol:

```python
class Tokenizer(Protocol):
    id: str
    def tokenize(self, text: str) -> list[str]: ...
    def count(self, text: str) -> int: ...
```

The default `WhitespaceTokenizer` (`id = "whitespace-1"`) splits on whitespace.
This is deliberately dependency-free and, crucially, **lossless on normalized
text**: `normalize_text` (Phase 3) collapses every whitespace run to a single
space and strips the ends, so for any normalized string `t`,
`" ".join(tokenize(t)) == t`. That property is what lets the chunker slice a token
list and rejoin the slice into a valid, exact substring — no text corruption when
splitting large pages.

Whitespace tokens under-count relative to the embedding model's wordpiece
tokenizer, which is acceptable for a v1 sizing heuristic. When Phase 4.b registers
a model-accurate tokenizer, it gets a **new `id`**, which changes the profile hash
(§4) and therefore produces a distinct, non-colliding set of chunks — no silent
resize of existing data.

## 4. Chunking profile and identity (`profile.py`)

A **chunking profile** is the complete set of knobs that determine chunk
boundaries:

| Field | Meaning |
| --- | --- |
| `version` | Algorithm version (`CHUNKING_VERSION`, currently `"chunk-1"`) |
| `target_tokens` | Target chunk size (default 750, from `Settings.chunk_target_tokens`) |
| `overlap_tokens` | Overlap between split sub-chunks (default 100) |
| `tokenizer_id` | Identity of the tokenizer used |

`ChunkingProfile` is a frozen dataclass that validates its budgets on
construction (`target > 0`, `0 <= overlap < target`). `profile.hash` is the SHA-256
of the canonical JSON of those four fields, so **any** knob change yields a new
identity.

`default_chunking_profile(target_tokens=750, overlap_tokens=100)` builds the active
profile over the whitespace tokenizer; the integration step will feed it the
`Settings` values.

**Deterministic chunk IDs.** `chunk_id(content_object_id, profile_hash, index)`
returns `uuid5(_CHUNK_NAMESPACE, f"{content_object_id}:{profile_hash}:{index}")`
with a fixed namespace UUID. Because a content object is content-addressed
(identical structured content → exactly one content object, from Phase 3),
re-running the chunker on the same content under the same profile reproduces
byte-identical IDs. This is the foundation of **Phase 4 exit criterion 1** — no
duplicate chunks or vector points on re-index — since the downstream upsert keys
on these IDs. Chunk IDs are intentionally embedding-agnostic; Phase 4.c folds the
embedding profile into the *vector point* ID, keeping the SQL chunk row stable
across embedding-model changes that don't alter chunk boundaries.

## 5. The algorithm (`chunker.py`)

`chunk_pages(pages, *, profile, tokenizer=DEFAULT_TOKENIZER) -> list[Chunk]`.

Page boundaries are the **preferred cut points** so a chunk maps to as few
physical pages as possible — this keeps citations precise (a chunk's page range is
what a search result reports). Two adjustments handle the size extremes:

**Small pages are packed.** Consecutive pages are greedily accumulated into one
chunk while their cumulative token count stays `<= target_tokens`. When the next
page would overflow, the pending pack is emitted (recording `page_start..page_end`
across the packed pages) and a new pack begins. This is the *only* case a chunk
spans more than one page, and only ever when the pages are individually small.

**Large pages are split.** A single page whose token count exceeds `target_tokens`
is windowed into overlapping sub-chunks: windows of `target_tokens` advancing by
`step = target_tokens - overlap_tokens`. Each sub-chunk stays inside that one page
(`page_start == page_end`), so overlap never leaks across a page boundary. The
final window stops as soon as it reaches the end of the token list — no duplicated
tail window.

Pseudocode:

```
pending = []            # packed small pages awaiting emit
for page in pages:
    tokens = tokenize(page.text)
    if not tokens: continue                    # skip blank pages/sections
    if len(tokens) > target:
        flush(pending)                          # close any pending pack first
        for window in windows(tokens, target, overlap):
            emit(window, page, page)            # split within one page
    else:
        if pending and pending_tokens + len(tokens) > target:
            flush(pending)                      # close pack before overflow
        pending.append(page)
flush(pending)
```

Emission (`_ChunkBuilder.emit`) assigns a **dense, 0-based ordinal**,
re-normalizes defensively (a no-op on already-normalized input), skips anything
that normalizes to empty, and records the SHA-256 `text_hash` of the chunk text.

### `Chunk` record

```python
@dataclass(frozen=True, slots=True)
class Chunk:
    index: int              # 0-based, dense
    text: str               # normalized chunk text
    token_count: int
    page_start: int | None  # physical page range (1-based, inclusive);
    page_end: int | None    #   None for pageless formats (TXT/MD/CSV/log)
    text_hash: str          # sha256 of text
```

IDs are **not** on the record — they are assigned at persist time via `chunk_id()`
from the content object + profile, keeping the pure library free of storage
identity.

## 6. Worked examples

| Input | Profile (target/overlap) | Result |
| --- | --- | --- |
| 5 pages × 30 tokens | 100 / 10 | 2 chunks: pages 1–3 (90 tok), pages 4–5 (60 tok) |
| 3 pages × 50 tokens | 50 / 10 | 3 chunks, one per page (no crossing) |
| 1 page × 250 tokens | 100 / 20 | 3 chunks (100, 100, 90), all page 7→7, 20-token overlap |
| pages with a blank one | 100 / 10 | blank skipped; range still spans real pages |
| pageless text sections | 100 / 10 | packed; `page_start/end = None` |

## 7. Guarantees delivered to later steps

- **Determinism.** Same pages + same profile ⇒ identical chunk sequence (text,
  boundaries, ordinals) ⇒ identical `chunk_id`s. Verified by test.
- **Idempotent re-index (exit criterion 1).** Deterministic IDs mean the Phase 4.c
  upsert overwrites in place; re-indexing an unchanged content object creates zero
  new chunks/points.
- **Citation precision.** Every chunk carries the physical page range it covers;
  cross-page chunks occur only for small pages, and split chunks never cross a
  page. Downstream `/search` resolves paths + pages from these ranges.
- **Profile isolation.** Changing the algorithm, budgets, or tokenizer changes the
  profile hash, so chunks from different logic are never mixed.
- **No source reopen.** Operates on `NormalizedPage` (also what
  `ArtifactStore.load_pages` returns), so integration reads the compressed
  artifact, not the original file.

## 8. Test coverage

`tests/unit/test_chunker.py` (13 tests): small-page packing within target;
page-boundary respect when pages fit; oversized-page split with in-page overlap;
blank-page skipping; dense 0-based ordinals; pageless (`None`) ranges; determinism
across runs; `text_hash` equals the normalized-content hash; empty input →
no chunks; `chunk_id` stability + sensitivity to content/profile/index; profile
hash sensitivity to every knob; budget validation; `Chunk` immutability.

Gate: full backend suite **122 pass** (109 prior + 13 new); ruff + mypy clean.

## 9. Follow-ups (later Phase 4 steps)

- **4.b** registers a model-accurate tokenizer (a new `tokenizer_id`/profile) and
  embeds `Chunk.text` (document vs. query paths).
- **4.c** persists chunk rows and upserts vector points keyed on
  `chunk_id` + embedding-profile-derived point ID; retrieval-only payload (no
  paths/tags/source names).
- **Integration** appends chunk → embed → upsert to `index_file._publish_success`
  inside the existing lease-fenced transaction; the terminal `indexed` state is
  unchanged.

## 10. Open decision resolved

Open decision #1 from `PHASE4_STATUS.md` (tokenizer source) is resolved for 4.a: a
pluggable `Tokenizer` with a pure whitespace default now; the model-accurate
tokenizer arrives in 4.b as a distinct profile, with no rework because no chunks
are persisted yet.
