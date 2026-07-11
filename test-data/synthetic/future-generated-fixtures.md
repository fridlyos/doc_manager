# Future generated PDF and OCR fixtures

These fixtures are specifications only. Phase 0 must not commit generated PDFs, rendered page images, fonts, or OCR output.

## Generation contract

- Generate into an ignored temporary directory from a reviewed script and a pinned container/tool lockfile.
- Use ISO A4 pages, fixed margins, a bundled open-license font pinned by SHA-256, UTC, fixed metadata strings, and no wall-clock timestamps, host names, random IDs, or network access.
- Normalize or suppress PDF creation/modification dates and document IDs. Record generator versions and SHA-256 hashes in a generated manifest.
- Regeneration with the same locked toolchain must produce identical text, page boundaries, and expected citations. Byte identity is desirable and must be asserted when the generator supports it.
- All generated content remains fictional and is deleted after the test run.

## `selectable/cobalt-kite-manual.pdf`

- Two text-selectable pages.
- The exact canonical line sequence is:
  1. `Cobalt Kite K-31 Reset Manual`
  2. `Press the blue latch, then wait 12 seconds before turning ring R-6.`
  3. `The inspection interval is 27 days, and the storage cradle is Birch-2.`
- Page 1 contains lines 1-2, and page 2 contains line 3. Do not add repeated headers, footers, or page-number text.
- Expected tests: PyMuPDF extracts exactly two one-based pages; a reset query cites page 1; an inspection query cites page 2; a chunk must not lose its page range.

## `selectable/cobalt-kite-manual-compact.pdf`

- Contains the same three canonical lines, in the same order, as `cobalt-kite-manual.pdf`, but places all lines on one text-selectable page.
- Expected duplicate classification: text-equivalent, not exact-file.
- Expected citation difference: both reset and inspection queries cite page 1 in this compact file.
- The pair must not share structured extraction records, page maps, chunk IDs, or vector identities merely because normalized words match. Reuse would require a separately specified metadata-safe design.

## `scanned/cobalt-kite-ocr-only.pdf`

- One 300-DPI image-only page with no PDF text layer.
- Visible title: `Cobalt Kite Scan Record`.
- Visible fact: `OCR marker OCR-KITE-73 was verified at 11:05 UTC.`
- MVP expectation: extraction returns `ocr_required`, creates no chunks, and keeps the catalog entry visible.
- Future OCR expectation: the marker query retrieves the visible fact and cites page 1.

## `scanned/blank-grid-ocr-only.pdf`

- One image-only page containing a pale fictional grid and no words.
- MVP expectation: `ocr_required`; future OCR expectation: an explicit empty-text result, never invented text or a citation.
