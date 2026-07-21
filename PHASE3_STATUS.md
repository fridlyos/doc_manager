# Phase 3 Progress Status

**Branch:** `phase-3-scanner-extraction` (cut from `main` @ `3476200`, Phase 2 merged).

Scope = TECHSTACK §14 "Phase 3: Scanner, Extraction, and Reconciliation".

## Deliverables

| # | Deliverable | Status |
| --- | --- | --- |
| 3.a | Safe traversal, filtering, hashing, and reconciliation | **✅ complete** |
| 3.b | PDF, TXT, MD, CSV, and log extractors | **✅ complete** |
| 3.c | Versioned normalization and compressed artifact storage | **✅ complete** |
| 3.d | Handle add/change/move/missing/restore states | ✅ delivered with 3.a reconciler |
| — | **Integration: `index_file` job + `content_objects`/`file_versions`** | **✅ complete** |
| 3.e | Document detail, errors, and manual retry/re-index API/UI | ⬜ not started |

## Exit criteria (whole phase)

- Synthetic filesystem lifecycle tests pass — ✅ scan/reconcile (3.a).
- Page numbers survive PDF extraction — ✅ (3.b; PdfExtractor preserves 1-based pages, tested).
- Errors are isolated per document and visible to the user — ⚠️ per-document error *codes*
  exist (3.b); surfacing them in the error queue/UI is 3.e.

## 3.b — completed 2026-07-21

Extractor library at `doc_manager/extraction/` (pure — no DB/vectors; the index_file
job in 3.c drives it).

**Delivered:**
- `base.py` — `Extractor` protocol + `ExtractedDocument`/`ExtractedPage` records (dense
  0-based `index`; 1-based `page_number` for PDFs, `None` for text formats) with
  `page_count`/`character_count`.
- `pdf.py` — PyMuPDF, one-based page numbers; distinguishes empty / encrypted / malformed /
  image-only (`no_extractable_text`).
- `text.py` — TXT/MD/log; charset-normalizer decoding, blank-line synthetic sections.
- `csv_.py` — row-aware `col: value` rendering, header repeated per 100-row section.
- `registry.py` — dispatch by extension; `SUPPORTED_EXTENSIONS` = {pdf,txt,md,log,csv};
  unknown → `None` (caller marks `unsupported`).
- `errors.py` — `ExtractionError` + stable `ExtractionErrorCode` (empty_file, encrypted,
  malformed, unsupported_encoding, no_extractable_text, unsupported_type).
- Deps: `pymupdf`, `charset-normalizer`; mypy override treats untyped PyMuPDF as Any.

**Tests:** 10 unit tests — PDF page-number preservation (exit criterion), encrypted /
image-only / empty / malformed PDF codes, text sectioning, CSV header-repeat + batching,
registry dispatch + unsupported. Full backend suite 91 pass; ruff/mypy clean.

**Deferred to 3.c:** normalization (text/structure hashes), compressed artifact storage,
and the `index_file` job that persists `file_versions`/`content_objects` from these results.

## 3.c — completed 2026-07-21

Versioned normalization + content-addressed compressed artifact storage (pure libraries,
consumed by the `index_file` job — see integration note below).

**Delivered:**
- `extraction/normalize.py` — `normalize(ExtractedDocument) → NormalizedDocument` with
  `NORMALIZATION_VERSION`. Two hashes by design: **text_hash** (pagination-insensitive —
  page boundaries dissolved, whitespace collapsed, NFC) for text-duplicate detection, and
  **structure_hash** (pagination-sensitive, over ordered records) that keeps text-equivalent
  files with different pagination distinct for citation correctness.
- `extraction/profile.py` — `extraction_profile_hash(name, version, settings)` — part of a
  content object's reuse key so an extractor/setting change yields a distinct artifact.
- `artifact_store/extracted_text.py` — `ArtifactStore`: gzip-compressed, content-addressed
  (`{norm_version}/{structure[:2]}/{structure}.{profile[:12]}.json.gz`), atomic temp-write +
  `os.replace`, idempotent reuse when the artifact already exists; stores page/section
  boundaries so re-chunking never reopens the source. `load`/`load_pages` round-trip.

**Tests:** 12 unit tests — normalization determinism, pagination-insensitive text_hash vs
sensitive structure_hash, whitespace/NFC; artifact content-addressing, gzip round-trip,
idempotent reuse, atomic write leaves no temp files. Full backend suite 99 pass; ruff/mypy
clean.

## a–c integration: `index_file` job — completed 2026-07-21

Wires extraction + normalization + artifact storage into the durable job engine and persists
content identity.

**Delivered:**
- Models `content_objects` (reuse key = unique on
  `structure_hash, extraction_profile_hash, normalization_version`; `text_hash` indexed for
  future text-duplicate reports) and `file_versions` (per-version bytes + extraction status +
  error), plus `catalog_entries.current_file_version_id`. Migration `0003` (circular FK added
  out-of-line; upgrade/downgrade round-trip verified).
- `jobs/handlers/index_file.py` — verify fingerprint (changed → transient, no mixed publish) →
  dispatch by extension (unknown → `unsupported`) → extract (ExtractionError → `failed` file
  version + entry, error visible) → normalize → reuse content object or write artifact + create
  one → link `file_version`, set entry `indexed`. Publication is lease-fenced like the scanner.
- Scanner reconcile now enqueues a deduped `index_file` per entry still `discovered`, atomically
  with reconciliation.

**Tests:** 4 PG-backed integration tests — scan→index persists content objects + versions +
artifacts; encrypted PDF → `failed` with `error_code`; unsupported extension → `unsupported`;
identical files share one content object (dedupe). Full backend suite **103 pass**; ruff/mypy
clean; conftest truncation updated for the new tables.

**Deferred to Phase 4:** chunking + embeddings + Qdrant upsert extend `index_file` after the
artifact step; the terminal `indexed` state stays the same.

## 3.a — completed 2026-07-21

Content-aware scanner + reconciliation. SHA-256 is the change authority.

**Delivered:**
- `core/hashing.py` — streaming SHA-256 (1 MiB blocks); OSError = "not observed this scan".
- `jobs/handlers/reconcile.py` — pure, DB-free reconciler producing add / changed /
  unchanged / metadata-only / **moved** / **restored** / **missing** transitions. Moves and
  restores are recognized by content hash, not path, so an indexed entry survives a rename
  without re-extraction.
- `jobs/handlers/scan_location.py` — hashes new/changed files (unchanged files carry the
  stored hash forward via a size+mtime fast path); default exclude-list for VCS/OS/system
  dirs on top of the location's extension/glob filters; raw-SQL reconcile replaced with the
  reconciler applied inside the existing fenced transaction.
- Migration `0002`: nullable `sha256` on `catalog_entries` (interim content authority until
  `file_versions` lands in 3.b), required `sha256` on `scan_observations`.

**Tests:** 10 reconciler unit tests (all lifecycle cases incl. copy-vs-move,
two-disappear-one-reappears); 2 new PG-backed integration tests (rename→move, mtime-touch
preserves indexed vs edit requeues); existing 5 scan tests still green. Full backend suite
81 pass; ruff/format/mypy clean; migration 0002 upgrade/downgrade round-trip verified.

**Deferred to 3.b:** full `file_versions` / `content_objects` (extraction concerns) — 3.a
stores `sha256` directly on `catalog_entries` as the interim content authority.

## Ops note

The live dev `docman` DB is still at `0001` on the Phase 2 image (consistent, not broken).
`docker compose up --build` + `alembic upgrade head` rolls it to `0002` to exercise 3.a live.
