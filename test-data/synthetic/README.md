# Synthetic Phase 0 corpus

This directory is the deterministic, non-sensitive test corpus for catalog, duplicate, retrieval, citation, and provider-policy contracts. Both directories under `source-roots/` simulate independent read-only mounts. Every name, date, code, organization, and fact is fictional.

## Fixture map

- `north-library/` contains TXT, Markdown, and CSV retrieval facts and defaults to external-generation `deny` in `ground-truth.json`.
- `south-archive/` contains TXT, Markdown, and CSV retrieval facts and defaults to external-generation `allow`.
- The two relay-maintenance files are byte-for-byte identical but have different relative paths, modeling an exact renamed copy.
- The two harbor-window files have different bytes but become identical under the fixture whitespace-normalization contract, modeling a normalized-text duplicate.
- Both roots contain `operations/status.txt` with different content, modeling a same-relative-path conflict.

Exact-file and text-equivalent groups are intentionally separate. Text equivalence is a duplicate-reporting fact only; it does not assert that structured extraction records, page maps, chunks, or vectors can be reused. Even exact bytes are only reuse candidates after format, extractor, profile, and structural compatibility checks.

`ground-truth.json` is the machine-readable contract. Citation fixture IDs refer to physical catalog entries, and pages are `null` because the committed corpus is text-only. For duplicate evidence, `citation_match: "any"` means any listed physical fixture may supply the canonical evidence; the resulting catalog record should still expose every active path. `citation_match: "all"` means all listed sources are needed, while `none` denotes insufficient evidence.

Do not auto-format or independently edit one member of an exact pair. UTF-8 and LF are the committed encoding and line-ending convention. The normalized-text pair assumes `trim + collapse every Unicode whitespace run to one ASCII space`; it intentionally does not prescribe the application's complete future normalization algorithm.

No binary document is committed. `future-generated-fixtures.md` specifies reproducible PDF and OCR-only cases to generate later in an isolated test build.
