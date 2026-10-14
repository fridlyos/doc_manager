# File-name index (name-first retrieval)

Most files in a personal library are named for what they are ("fridge from 2018 -
4 years warranty.pdf"), often more plainly than their text — a scanned receipt's
OCR may never say "fridge" or "warranty". Retrieval therefore searches file names
before passages.

## Storage

- A second Qdrant collection, `doc_names__<model-slug>__<profile-hash>`, bound to
  the active embedding profile like the chunk collection.
- One point per **catalog entry** (point id = catalog entry id), so duplicate
  files with different names each get their own name vector.
- Embedded text: `vectors.names.file_name_text(relative_path)` — extension and
  sync-client conflict suffixes (`_DiskStation_Dec-10-1230-2022_DownloadConflict`)
  removed, separators turned into spaces, file name first, then the folder path.
- Payload: `catalog_entry_id`, `content_object_id`, `source_location_id`,
  `name_hash` (SHA-256 of the embedded text). **No names or paths are stored**,
  consistent with the chunk payload rule (TECHSTACK 5.9).

## Keeping it current

- `index_file` upserts the entry's name point right after it is published as
  indexed (best effort; a failure only logs `name_index_failed`).
- Every scan enqueues `sync_name_index` for its location (dedupe key
  `names:{location_id}`). It embeds indexed entries with no point or a changed
  `name_hash` (renames/moves) and deletes points whose entry is no longer indexed.
  This is also the backfill for files indexed before the name index existed: run
  a scan of each location.
- `remove_stale_vectors` drops name collections of retired embedding profiles.

## Query

`RetrievalService.search`:

1. Embed the query; run the regular hybrid passage search.
2. Search the name collection (10 candidates). Keep names scoring at least
   `name_match_min_score` (0.75) and within `name_match_margin` (0.05) of the best.
3. Re-check those entries in PostgreSQL: only entries whose state is `indexed`
   **now** count, using their current content object — a failed original never
   leads, while its successfully indexed copy can. Respect the request's filters.
4. Up to `name_match_max_files` (3) files lead the results with up to
   `name_match_chunks_per_file` (2) of their best passages each, in name-match
   order; the regular ranking follows without duplicates, cut to `top_k`.

Thresholds were tuned against the live library with `BAAI/bge-small-en-v1.5`:
"what's my fridge warranty" → fridge receipt 0.844 (next name 0.721);
"Albert's last job" → best name 0.721 (wrong file, correctly excluded);
an unrelated question → best name ≈ 0.56.

Chat uses the same search, so name-matched files reach the model's evidence first.
