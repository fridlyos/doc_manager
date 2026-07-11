# ADR 0003: Store Extracted Text as Content-Addressed Compressed Artifacts

- **Status:** Proposed
- **Date:** 2026-07-11
- **Decision owners:** Project maintainers

## Context

Re-chunking or re-embedding should not require reopening every source file. Full extracted text can be large and is awkward to update/query as PostgreSQL rows, but relying only on original documents makes recovery and pipeline upgrades expensive. Duplicate physical files should reuse one extraction result.

## Decision

Persist successful extraction output as immutable Zstandard-compressed JSON artifacts keyed by structured extraction identity. PostgreSQL stores artifact metadata, counts, text/structure hashes, a short preview, and its relative path; it does not store every full extracted page.

Two hashes serve different purposes:

- `text_hash` groups text-equivalent documents for duplicate reporting after pagination-insensitive duplicate normalization.
- `structure_hash` covers the ordered normalized section/page envelope and is the reuse identity for artifacts, chunks, vectors, and page citations.

Documents with identical words but different page boundaries may share `text_hash` but must not share chunks/vectors unless `structure_hash` also matches.

### Logical artifact path

```text
<artifact_root>/<extraction_profile_hash>/<normalization_version>/<structure_hash[0:2]>/<structure_hash>.json.zst
```

The SQL record also stores `artifact_sha256` for the compressed bytes so storage corruption or an incomplete NAS copy can be detected independently of `text_hash`.

### Version 1 artifact envelope

```json
{
  "schema_version": 1,
  "text_hash": "sha256-of-normalized-text",
  "structure_hash": "sha256-of-canonical-section-envelope",
  "extraction_profile_hash": "sha256-of-extractor-settings",
  "extractor": {"name": "pymupdf", "version": "..."},
  "normalization_version": "text-v1",
  "document_metadata": {"title": null, "author": null},
  "sections": [
    {"index": 0, "kind": "page", "page_number": 1, "text": "..."}
  ],
  "warnings": []
}
```

Source paths are excluded because one content object can have many current paths and paths can change.

### `text-v1` normalization

Normalize each stored section, in order:

1. Unicode NFC normalization.
2. Convert CRLF and CR line endings to LF.
3. Remove trailing horizontal whitespace from each line.
4. Remove leading/trailing blank lines within each extracted section.
5. Preserve case, internal whitespace, punctuation, and section/page order.
6. Serialize ordered section kind, one-based page number when present, and normalized section text as canonical JSON for `structure_hash`.

For duplicate-only `text_hash`, concatenate normalized section text, collapse every Unicode whitespace run (including page boundaries) to one ASCII space, trim, and hash the result. Case and punctuation remain significant.

Stored-section normalization is intentionally conservative. Duplicate normalization is deliberately more tolerant of pagination/line wrapping but does not control citation/vector reuse. Any change requires a new normalization version.

### Publication and lifecycle

- Write to a same-filesystem staging path, flush/close, checksum, and publish with an atomic rename where supported.
- On an SMB artifact store, a completion marker/checksum makes partially copied artifacts invalid even if atomic rename guarantees differ.
- A conflicting existing path is accepted only if its compressed checksum and structured envelope identity validate.
- Artifacts are deleted only after no retained file version/content object/backup set references them and the cleanup grace period expires.
- Original files remain read-only and outside this store.

## Consequences

### Positive

- Re-chunking/re-embedding avoids repeated PDF/text extraction.
- Duplicate paths share one artifact.
- PostgreSQL stays focused on operational metadata.
- Artifacts can be backed up, checksummed, and inspected independently.

### Negative

- Adds filesystem lifecycle, schema-version, compression, and garbage-collection logic.
- Artifact and SQL publication cannot be one atomic transaction; reconciliation is required.
- Sensitive extracted text exists in another protected storage location.

## Alternatives considered

- **Full text in PostgreSQL:** transactionally convenient but increases database/backup size and large-row I/O.
- **Only Qdrant payload text:** makes the semantic index an unrecoverable content store and complicates re-chunking.
- **Always re-extract sources:** simple storage model but slow, fragile when sources are offline, and wasteful for duplicates.
- **Uncompressed JSON:** easier inspection but materially larger NAS/backup footprint.
