# Performance baseline

A documented local baseline on a representative synthetic corpus — **not** a CI
gate and **not** an SLO (open decision #3). The goal is a reproducible method and
a recorded number per environment, so a regression or a tuning change is visible.
Capture the corpus size + hardware alongside every result; numbers are only
comparable within the same environment.

## Corpus

Reuse the in-repo synthetic corpus at `test-data/synthetic/source-roots`
(`north-library`, `south-archive`) with `ground-truth.json` for known queries. For
a larger run, grow the corpus (more files / larger PDFs) and record the new size.
Document: file count, total MB, and the mix of PDF/TXT/MD.

## What to measure

| Metric | How | Source |
| --- | --- | --- |
| Scan throughput | files/s during a `scan_location` | jobs API progress + timestamps |
| Index throughput | files/s and MB/s during `index_file` fan-out | jobs API; worker logs |
| Embedding rate | chunks/s (derived from index timing + chunk count) | worker logs (`file_indexed`) |
| Search latency | P50 / P95 over N queries | `scripts/perf-measure.sh` |
| Ask latency | retrieval_ms / generation_ms / total_ms | Ask response `timing` block |

## Method

1. Start the stack and pull the chat model (see
   [`model-setup.md`](model-setup.md)); pre-warm the embedding model with a small
   scan so the first-run download is not counted
   ([`model-setup.md`](model-setup.md) cache caveat).
2. Add the synthetic corpus as a source location and scan + index it. Read scan
   and index throughput from the jobs API — each job's `progress` counters plus its
   `created_at`/completion timestamps give files over wall-clock:
   ```bash
   curl -s http://127.0.0.1:8000/api/v1/jobs | python3 -m json.tool
   ```
   The worker's structured `file_indexed` logs (pages, chunks per file) give the
   embedding rate.
3. Measure search + Ask latency against the indexed corpus:
   ```bash
   API=http://127.0.0.1:8000 N=30 QUERY="the renewal clause covers december terms" \
     ./scripts/perf-measure.sh
   ```
   The harness warms up once (excluded), samples `N` searches for P50/P95, and runs
   one Ask, printing the server-side `retrieval_ms`/`generation_ms`/`total_ms`. It
   is a dev tool, not part of the runtime image.

## Results (fill per run)

| Date | Corpus (files / MB) | Hardware (CPU / RAM / GPU) | Scan f/s | Index f/s | Search P50 / P95 (ms) | Ask total (ms) | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| _pending local run_ | | | | | | | |

> Numbers are pending a run on a live stack (this environment had no Docker/PG).
> Record at least one baseline row before the MVP release and keep prior rows for
> comparison.

## Tuning notes

- **Embedding memory/throughput:** `DOCMAN_EMBEDDING_BATCH_SIZE` (default 256)
  trades worker memory for embedding throughput; the worker memory limit is
  `DOCMAN_WORKER_MEM` (default 4g).
- **Worker concurrency:** indexing is a durable fan-out (one `index_file` per
  file); run more worker replicas or raise `DOCMAN_WORKER_CPUS` to parallelize.
- **Evidence size vs Ask latency/cost:** `DOCMAN_ASK_MAX_EVIDENCE_BLOCKS` (12) and
  `DOCMAN_ASK_MAX_CHUNKS_PER_CONTENT` (3) bound retrieval; output caps are
  `DOCMAN_GENERATION_MAX_OUTPUT_TOKENS` (local) /
  `DOCMAN_EXTERNAL_MAX_OUTPUT_TOKENS` (OpenAI). See
  [`provider-operations.md`](provider-operations.md).
- **Search:** `top_k` and `DOCMAN_SEARCH_SCORE_THRESHOLD` affect candidate count;
  larger `top_k` raises latency modestly.

## Related

- [`model-setup.md`](model-setup.md) · [`provider-operations.md`](provider-operations.md)
  · [`runbook.md`](runbook.md).
