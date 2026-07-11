# ADR 0001: Separate Physical Files from Canonical Content

- **Status:** Proposed
- **Date:** 2026-07-11
- **Decision owners:** Project maintainers

## Context

One document's bytes or extracted text can appear at multiple paths, under multiple names, and in multiple source locations. A path can also move, disappear, or later point to different bytes. If vectors and chunks belong directly to a path row, copies create redundant embeddings and path moves make citations stale.

The catalog must distinguish physical filesystem observations from reusable extracted content while retaining audit history.

## Decision

Use four separate identity layers:

1. `catalog_entry`: one relative path within one source location.
2. `file_version`: bytes observed for a catalog entry at a point in time, identified by SHA-256 plus observation metadata.
3. `content_object`: canonical structured extraction, identified by structure hash and extraction/normalization profile; it also carries a text-equivalence hash.
4. `chunk`: deterministic retrieval unit belonging to a content object and chunking profile.

### Identity rules

- Public catalog, version, content, job, and location IDs are UUIDv4.
- Chunk IDs and Qdrant point IDs are UUIDv5 derived from a fixed application namespace plus content-object identity, chunking-profile hash, embedding-profile hash, and chunk index.
- `(source_location_id, relative_path)` uniquely identifies a physical catalog entry.
- A catalog entry has at most one current file version and retains older versions.
- A content object is unique for its structured extraction hash plus extraction/normalization profile identity.
- A chunk is unique for its content object, chunking profile, and chunk index.

### Duplicate and move semantics

- Same file SHA-256 across current versions is an exact-file duplicate.
- Different file hashes with the same duplicate-normalized text hash are text-equivalent duplicates.
- Text-equivalent documents reuse chunks/vectors only when their structured extraction hashes also match. Different page/section boundaries remain separate so citations stay correct.
- A new path with a known file hash is a copy or move candidate and reuses existing canonical content.
- A missing old path and new same-hash path in the same reconciliation window is reported as a move candidate; history is not rewritten.
- An mtime-only change with unchanged SHA-256 updates the observation without re-extraction.

### Citation semantics

Qdrant returns content/chunk identities, never authoritative paths. The retrieval service resolves all active catalog entries referencing that content through PostgreSQL at query time. This keeps citations current after copies and moves. Missing/historical paths are labeled rather than silently discarded when audit history is requested.

## Invariants

- Deleting or missing one physical copy does not delete shared vectors while another active entry references the content.
- Publishing a new file version never mutates an older version.
- Canonical structured content and chunks are reusable but immutable under a processing profile.
- A processing-profile change produces new content/chunk/vector identities or an explicit rebuild; incompatible data is never mixed.
- Duplicate groups are rebuildable projections over authoritative hashes.

## Consequences

### Positive

- Exact copies share extraction, chunks, and vectors.
- Paths can change without stale vector payload paths.
- Duplicate and location-coverage reporting become direct catalog queries.
- History and restore behavior are explicit.

### Negative

- Retrieval requires a PostgreSQL join after Qdrant search.
- Reconciliation and garbage collection need reference counting/queries.
- The schema has more entities than a path-centric MVP.

## Alternatives considered

- **One `documents` row per path:** simpler initially, but duplicates vectors and couples citations to mutable paths.
- **One row per file hash:** handles exact copies but loses independent path lifecycle and text-equivalent duplicates.
- **Paths embedded permanently in Qdrant payloads:** fast reads but stale after moves and makes Qdrant an accidental catalog.
