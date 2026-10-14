# OCR lane and worker lanes

OCR (Tesseract on rasterised pages) costs seconds to minutes per document, while
text extraction costs milliseconds. Running both in one FIFO queue let a single
long scanned PDF stall scans, name syncs and every other file behind it.

## Jobs

- `index_file` never OCRs. With OCR enabled, the PDF extractor runs in
  *deferred* mode: at the first page without a text layer it raises
  `OcrRequired`. The handler then enqueues `ocr_file` for the entry (dedupe
  `ocr:{entry_id}`, same lineage), marks the entry `queued`, and completes.
  Rescans skip `queued` entries; a content change resets them to `discovered`.
- `ocr_file` runs the same handler with OCR enabled and publishes the final
  `indexed` / `failed` state. The extractor version is the same in both modes
  (`pdf-1-ocr`), so content identity does not change.
- Default claim priority per job type (`DEFAULT_JOB_PRIORITY`): `scan_location`
  and `sync_name_index` 10, `ocr_file` -10, everything else 0. The queue claims
  by priority, then age.

## Worker lanes (`DOCMAN_WORKER_LANE`)

| lane | claims |
|---|---|
| `general` | everything except OCR; after `worker_ocr_join_idle_seconds` (60 s) idle, OCR too (other work still wins on priority) |
| `ocr` | OCR first; other work when no OCR is waiting |
| `any` | everything by priority (single-worker default) |

`compose.yaml` runs `worker` (general lane, `DOCMAN_GENERAL_WORKERS` replicas,
default 2) and `worker-ocr` (1 replica), each capped at `DOCMAN_WORKER_CPUS`
(4) and `DOCMAN_WORKER_MEM` (3g). Every worker also runs the reaper and
scheduler loops; both are safe to run concurrently (scan enqueues coalesce on
the open-scan-per-location rule).
