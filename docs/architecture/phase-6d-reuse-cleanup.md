# Phase 6.d — Canonical Reuse Invariant & Delete/Restore Convergence

**Status:** ✅ complete · **Branch:** `phase-6-reindex-duplicates` · **Spec:** TECHSTACK §5.4, §14 (Phase 6.d)

Formalizes the content/vector **reuse invariant** and closes the **delete half** of
convergence (exit criterion 1): a deleted file's vectors are retired, and a restore
rebuilds them.

---

## 1. Reuse invariant (already implemented, now tested)

`index_file` reuses a content object — and its chunks + Qdrant points — only when
the **structure hash** and the extraction/normalization/chunking/embedding profiles
match. Consequences, now asserted end-to-end:

- **Structure-equivalent paths share one content object** → they share
  citation-bearing chunks (verified: two identical PDFs → one `content_objects`
  row; all chunks reference it).
- **Text-equivalent files with different pagination do *not* share chunks** →
  separate content objects for citation correctness (verified: two PDFs with the
  same words split across a different number of pages → same `text_hash`, **different**
  `structure_hash`, two content objects, disjoint chunk sets).

## 2. Orphan cleanup (`remove_stale_vectors`, extended)

`remove_orphan_content` deletes every content object **not referenced by any
`indexed` catalog entry** (its files were deleted, so every referencing entry is
`missing`): it removes the Qdrant points (active collection) and deletes the
content object, which cascades its chunk rows. Combined with the collection-drop
from 6.b, `remove_stale_vectors` now retires both profile-superseded collections and
orphaned content. Safe to re-run (absent orphans are a no-op).

## 3. Automatic delete-convergence after a scan

When the reconciler marks entries `missing` (files deleted), the scan handler now
enqueues a deduped `remove_stale_vectors` job, so the vector store converges
**after a scan** without a manual step. A subsequent scan that finds the file again
transitions `missing → discovered` (Phase 3.a) and re-indexes, which recreates the
content object, chunks, and points by hash — a full round-trip:

```
index → delete file → scan (entry missing, vectors retired)
      → restore file → scan (entry discovered → re-index, vectors rebuilt)
```

## 4. Verification

- **Reuse (PG + in-memory Qdrant), 2 tests:** structure-equivalent PDFs share one
  content object + chunks; text-equivalent-different-pagination PDFs get two content
  objects (same `text_hash`, different `structure_hash`) with disjoint chunks.
- **Convergence, 1 test:** index → delete + rescan → **content object, chunks, and
  points all gone**, entry `missing`; restore + rescan → entry `indexed`, content /
  chunks / points **rebuilt**.

Gate: full backend suite **265 pass, 1 skipped**; ruff + mypy clean.

## 5. Follow-ups

- **6.e** — Duplicates + Coverage UI over the 6.c endpoints, plus surfacing
  re-index / rebuild / stale-vector actions.
- Chunking-profile-superseded chunk rows (different `chunking_profile_hash`) are not
  yet swept — they are harmless (their points live in a dropped/rewritten collection)
  and can be added to orphan cleanup if a chunking-profile change is exercised.
- A scheduled `remove_stale_vectors` maintenance tick could complement the
  scan-triggered enqueue.
