# doc_manager backend

FastAPI API and background worker for the local-first RAG document manager.

Phase 1 delivers the runnable skeleton: configuration, structured logging,
liveness/readiness health, storage/mount preflight checks, and the worker
entrypoint. Catalog schema, scanning, embeddings, and RAG arrive in later
phases.

## Local development

```bash
uv sync                                   # create venv, install deps + dev tools
uv run doc-manager-api                    # start API on DOCMAN_BIND_HOST:DOCMAN_PORT
uv run doc-manager-worker                 # start the worker loop
uv run pytest                             # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

The single package is shared by the API and worker containers; separate
containers provide failure isolation without duplicating domain logic.
