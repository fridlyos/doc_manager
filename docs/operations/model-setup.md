# Model setup

The system uses two independent models: a **chat model** for generation
(optional; via Ollama or OpenAI) and a local **embedding model** for indexing and
search (required for anything beyond raw catalog browsing). This guide covers
pulling/pre-warming both and the one operational gotcha — the embedding model
cache is not persisted by default.

For *which* provider to configure and the two external-opt-in gates, see
[`provider-configuration.md`](provider-configuration.md) and
[`external-processing.md`](external-processing.md). This guide is about getting
the model bits onto the machine.

## Chat model (generation)

Generation is optional: with no ready provider the system runs in **search-only**
mode and never fails readiness.

### Local: Ollama (default, native Windows)

Ollama runs **natively on Windows**, not in a container; containers reach it at
`http://host.docker.internal:11434`.

1. Install Ollama for Windows and start it (listens on `127.0.0.1:11434`).
2. Pull the model named by `DOCMAN_OLLAMA_CHAT_MODEL` (default `llama3.1:8b`):
   ```
   ollama pull llama3.1:8b
   ```
   This is a deliberate setup-time network download — pick a model that fits
   available RAM/VRAM.
3. Verify the containers can reach it:
   ```bash
   docker compose exec api python -c \
     "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').status)"
   ```
   `200` → `system/status` reports `ollama: up`. If Ollama is stopped, readiness
   is unaffected and the system stays search-only; it never falls back to an
   external provider.

### External: OpenAI (opt-in)

No local model to pull — you provide an API key as a Docker secret and enable the
two gates. See [`external-processing.md`](external-processing.md). Requires
`DOCMAN_OPENAI_MODEL` (no assumed default).

## Embedding model (required for indexing/search)

The worker loads a local **FastEmbed** ONNX model named by
`DOCMAN_EMBEDDING_MODEL` (default `BAAI/bge-small-en-v1.5`). There is no separate
pull step: the model **downloads automatically on the first embed** (first scan
or first search that needs embeddings) and is then cached in-process for the life
of the worker (`load_fastembed`, once per process).

### Cache is not persisted by default — plan for the first-run download

The worker does **not** mount a persistent model-cache volume. Consequences:

- The first indexing/search after a cold start pays the one-time model download
  before it makes progress. Expect the first job to appear slow.
- Recreating the worker container (`docker compose build`, `up --build`, or
  `make nuke`) discards the in-container cache, so the model **downloads again**
  on the next embed. This is a network action at an unexpected time, and it fails
  if the host is offline.

To avoid repeat downloads, add a cache volume via a compose override so the
FastEmbed/HuggingFace cache survives container recreation, e.g. mount a named
volume at the worker's cache directory (FastEmbed uses the HuggingFace hub cache;
`HF_HOME` controls its location). Treat this as a deployment choice, not a
default — document it in your override if you add it.

### Pre-warm (optional)

To pay the download up front rather than on a user's first query, trigger a small
scan right after `make up` (the default synthetic corpus works) and watch the
worker log for `fastembed_model_loaded`. Once logged, the model is resident and
subsequent embeds are fast.

### Changing the embedding model forces a re-index

The embedding model + vector size define the **embedding profile** recorded in
the catalog. Changing `DOCMAN_EMBEDDING_MODEL` makes existing vectors
incompatible and requires a full rebuild — see the re-index section of
[`upgrade-migration.md`](upgrade-migration.md).

## Related

- [`provider-configuration.md`](provider-configuration.md) — provider selection
  and connectivity.
- [`external-processing.md`](external-processing.md) — enabling OpenAI safely.
- [`upgrade-migration.md`](upgrade-migration.md) — when a model change forces a
  re-index.
- [`troubleshooting.md`](troubleshooting.md) — model-load and connectivity
  failures.
