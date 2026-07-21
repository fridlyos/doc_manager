# Local RAG Document Search and Cataloging System

## Goal

Build a local-first document search and cataloging system that indexes documents from one or more local/NAS locations, stores searchable semantic vectors in a vector database, tracks document metadata in SQL, and uses Ollama locally to answer questions with source file paths and citations.

The system must run locally. Documents, metadata, embeddings, vector indexes, and LLM inference should remain on local hardware unless a future configuration explicitly enables cloud services.

## Core Use Cases

1. Ask natural-language questions about indexed documents.
2. Return source document paths, page numbers, and relevant snippets.
3. Run all indexing, search, and LLM inference locally.
4. Re-index files when documents are added, changed, moved, or deleted.
5. Index multiple document locations.
6. Detect duplicates across locations by file hash and extracted-text hash.
7. Optionally synchronize/catalog mirrored document locations.
8. Provide a UI for search, chat, document browsing, index status, and admin actions.

## Recommended Stack

| Area | Tool | Purpose |
| --- | --- | --- |
| Local LLM | Ollama | Runs local chat/completion models for final answers |
| Embeddings | Local embedding model via FastEmbed or sentence-transformers | Converts chunks and questions into vectors |
| Vector DB | Qdrant | Stores embeddings and performs semantic search |
| SQL DB | PostgreSQL | Stores document catalog, paths, hashes, tags, jobs, and audit data |
| API | FastAPI | Local backend API for ingest, search, chat, and admin actions |
| UI | React + Vite, or simple server-rendered FastAPI/Jinja for MVP | Web interface |
| PDF extraction | PyMuPDF | Extracts text, page numbers, and metadata from PDFs |
| Text extraction | Python stdlib/pathlib + charset detection as needed | Handles `.txt`, `.md`, `.csv`, logs, and simple text files |
| OCR fallback | Tesseract or Unstructured, optional | For scanned PDFs/images |
| Runtime | Docker Compose | Runs Qdrant, PostgreSQL, API, worker, and UI locally |

## Running Locally (Windows + Docker Desktop)

The stack runs as Docker containers (PostgreSQL, Qdrant, API, worker, and an
optional dev UI). These steps target **native Windows** with **Docker Desktop**
using the WSL 2 Linux engine and Linux containers. Run the commands from
**PowerShell** in the repository root unless noted.

### Prerequisites

- **Docker Desktop** running, set to the **WSL 2 Linux engine** / Linux
  containers. Verify: `docker compose version`.
- **Ollama** (optional, for the answer/chat step) installed natively on Windows
  and running (`ollama serve`, then `ollama pull llama3.1:8b`). Containers reach
  it at `http://host.docker.internal:11434` — already set in `.env.example`.
  The API/worker start fine without it; the system reports `search_only` until a
  generation provider is reachable.

### 1. Create the environment file

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and set `DOCMAN_POSTGRES_PASSWORD` to a local value (the
placeholder is `change-me-locally`). Defaults work with **no NAS**: the worker
mounts the in-repo synthetic corpus at `./test-data/synthetic/source-roots`
read-only. To index real documents, point `DOCMAN_NAS_DOCUMENTS_HOST_PATH` at a
Windows path using forward slashes (e.g. `Z:/Documents`); that path must exist
and carry the sentinel file `.docman-source-id` before startup.

### 2. Build and start the stack

```powershell
docker compose up -d --build     # postgres, qdrant, api, worker (detached)
docker compose ps                # service status / health
docker compose logs -f api worker
```

- **API:** http://127.0.0.1:8000 (bound to localhost only)
- PostgreSQL and Qdrant are **internal-only** — no published ports.

### 3. Verify it is up

```powershell
curl http://127.0.0.1:8000/health/live      # {"status":"alive"}
curl http://127.0.0.1:8000/health/ready      # 200 once postgres + qdrant are healthy
curl http://127.0.0.1:8000/api/v1/system/status
```

`/health/ready` returns **503** until PostgreSQL and Qdrant are up; optional
generation providers (Ollama/OpenAI) being down does **not** fail readiness.

### 4. Optional: start the dev UI

```powershell
docker compose --profile dev up -d --build   # Vite dev server
```

UI at http://127.0.0.1:5173 (proxies `/api` and `/health` to the API). In
production the API container serves the built static assets instead.

### 5. Stop / reset

```powershell
docker compose down       # stop, keep data volumes
docker compose down -v    # stop and DELETE all local data (postgres + qdrant)
```

### Make shortcuts (Git Bash or WSL)

A `Makefile` wraps these commands. `make` is not available in stock PowerShell;
run it from **Git Bash** or **WSL**:

```bash
make up          # docker compose up -d --build (+ copies .env if missing)
make up-dev      # also start the Vite dev UI
make ps          # service status
make logs        # tail api + worker
make preflight   # ./scripts/check.sh — .env, source mount sentinel, backup path
make down        # stop (keep volumes)
make nuke        # stop and delete volumes
```

### External generation (OpenAI, opt-in)

External generation is **off** by default (`DOCMAN_EXTERNAL_LLM_ENABLED=false`).
To enable it deliberately, layer the override file — the OpenAI key is mounted as
a Docker secret, never placed in `.env`:

```powershell
docker compose -f compose.yaml -f compose.external-llm.yaml up -d --build
```

See `docs/operations/provider-configuration.md` and
`docs/operations/runbook.md` for details.

## Architecture

```text
NAS / local document locations
        |
        v
File scanner
        |
        v
Fingerprinting: path, size, mtime, sha256
        |
        v
Extractor: PDF/text/OCR fallback
        |
        v
Chunker: page-aware, token-aware text chunks
        |
        v
Embedding model
        |
        +--------------------+
        |                    |
        v                    v
PostgreSQL metadata       Qdrant vectors
        |                    |
        +---------+----------+
                  |
                  v
FastAPI retrieval layer
                  |
                  v
Ollama local LLM
                  |
                  v
Answer with citations, paths, pages, snippets
```

## Major Components

### 1. File Scanner

Scans configured locations such as:

```text
/mnt/nas/legal
/mnt/nas/contracts
/mnt/nas/manuals
D:\Documents
```

Responsibilities:

- Walk folders recursively.
- Include supported extensions.
- Exclude temporary/system folders.
- Record size, modified time, path, and content hash.
- Detect added, changed, moved, and deleted files.
- Queue extraction/indexing jobs.

### 2. Document Extractor

Supported first:

- `.pdf`
- `.txt`
- `.md`
- `.csv`
- `.log`

Later:

- `.docx`
- `.xlsx`
- `.pptx`
- scanned PDFs via OCR

Extractor output should preserve:

- source file path
- page number when available
- extracted text
- document metadata
- extraction errors

### 3. Chunker

Splits extracted text into chunks suitable for retrieval.

Recommended MVP defaults:

- chunk size: 500-1,000 tokens
- overlap: 50-150 tokens
- preserve document ID, page range, chunk index
- avoid splitting mid-page when possible

### 4. Embedding Service

Creates vectors for each chunk and for each user query.

Recommended local embedding models:

- `BAAI/bge-small-en-v1.5` for small/fast English search
- `BAAI/bge-base-en-v1.5` for better quality
- `BAAI/bge-m3` for multilingual or mixed document collections

The embedding model and vector size must be stored in metadata so the system knows when a full re-index is required.

### 5. Qdrant Vector DB

Stores chunk embeddings and search payloads.

Example payload:

```json
{
  "document_id": "uuid",
  "chunk_id": "uuid",
  "source_location": "nas_contracts",
  "path": "/mnt/nas/contracts/vendor-a.pdf",
  "file_name": "vendor-a.pdf",
  "page_start": 12,
  "page_end": 13,
  "sha256": "file-content-hash",
  "text_hash": "chunk-text-hash"
}
```

Qdrant is used for semantic search only. It should not be the primary catalog or job database.

### 6. PostgreSQL Catalog

PostgreSQL stores durable operational data:

- configured document locations
- documents and paths
- file hashes
- extracted-text hashes
- chunks
- Qdrant point IDs
- ingestion job status
- duplicate groups
- tags and notes
- user/search history if enabled

PostgreSQL should not store original PDFs. Those remain on the NAS/filesystem.

### 7. Retrieval and RAG

Question flow:

```text
User asks question
  -> embed question
  -> search Qdrant
  -> fetch metadata from PostgreSQL
  -> build context with snippets and citations
  -> send prompt to Ollama
  -> return answer with source paths/pages
```

The answer should include:

- concise answer
- source file paths
- page numbers when available
- quoted snippets or context summary
- confidence/limitations when evidence is weak

### 8. UI

Required UI screens:

- Search/chat screen
- Result list with paths, page numbers, snippets, and open/copy path action
- Document catalog browser
- Duplicate document view
- Indexing status dashboard
- Location management screen
- Re-index button per file/location/all
- Error queue for failed extractions

MVP UI can be simple. The first goal is trustworthy search and clear citations.

## Data Model Draft

### `source_locations`

```text
id
name
root_path
enabled
scan_interval_minutes
created_at
updated_at
```

### `documents`

```text
id
source_location_id
path
file_name
extension
mime_type
size_bytes
mtime
sha256
text_hash
status
indexed_at
deleted_at
error_message
created_at
updated_at
```

### `chunks`

```text
id
document_id
chunk_index
page_start
page_end
text_hash
text_preview
token_count
qdrant_point_id
created_at
updated_at
```

### `duplicate_groups`

```text
id
duplicate_type      # file_hash or text_hash
hash_value
created_at
```

### `duplicate_group_members`

```text
duplicate_group_id
document_id
```

### `ingestion_jobs`

```text
id
job_type
source_location_id
document_id
status
started_at
finished_at
error_message
```

## Re-indexing Strategy

Each scan compares current filesystem state to the catalog.

Re-index when:

- file path is new
- size changed
- modified time changed
- sha256 changed
- extraction version changed
- chunking settings changed
- embedding model changed

Do not re-index when:

- path changes but sha256 already exists; treat as move/copy
- mtime changes but sha256 is unchanged, unless configured otherwise

Delete handling:

- mark missing files as deleted in PostgreSQL
- remove or tombstone related vectors in Qdrant
- keep history if audit mode is enabled

## Duplicate Detection

Detect duplicates at two levels:

1. File duplicate: same `sha256`
2. Text duplicate: same normalized extracted `text_hash`

This catches:

- identical copied files
- renamed files
- same PDF stored in multiple locations
- different PDF wrappers with identical extracted text

The UI should show duplicate groups and all known paths.

## Multi-location Synchronization

Initial scope should be catalog synchronization, not file copying.

MVP:

- scan multiple roots
- detect same files across roots
- show duplicates and missing copies
- report location coverage

Later optional file synchronization:

- policy-based copy/move
- dry-run preview
- conflict handling
- never delete files automatically without explicit confirmation

## Local Storage Layout

Recommended NAS/app storage:

```text
/volume/doc-rag/
  documents/              # optional managed documents root
  app-data/
    postgres/
    qdrant/
    extracted-text/
    logs/
    backups/
```

If the NAS supports Docker, run PostgreSQL and Qdrant directly on the NAS.

If the NAS is only an SMB/NFS share, prefer running PostgreSQL and Qdrant on a local machine or mini server with local SSD storage, then back up to the NAS. Live database files over network shares can be unreliable and slow.

## Docker Services

Planned services:

```text
postgres
qdrant
ollama
api
worker
ui
```

Optional:

```text
redis      # job queue, only if background work outgrows a simple DB queue
nginx      # reverse proxy, only if exposing to LAN users
```

## API Draft

```text
GET  /health
GET  /api/v1/locations
POST /api/v1/locations
POST /api/v1/locations/{id}/scan
GET  /api/v1/documents
GET  /api/v1/documents/{id}
POST /api/v1/documents/{id}/reindex
GET  /api/v1/duplicates
POST /api/v1/search
POST /api/v1/ask
GET  /api/v1/jobs
GET  /api/v1/jobs/{id}
```

## Security and Privacy

- Default to LAN/local-only access.
- Do not expose Qdrant, PostgreSQL, Ollama, or the API directly to the internet.
- Add authentication before multi-user use.
- Keep source documents read-only for the indexing service.
- Log paths and job status, but avoid storing full sensitive text in logs.
- Make cloud LLM/embedding providers opt-in only.

## MVP Milestones

### Phase 1: Local Infrastructure

- Docker Compose for PostgreSQL and Qdrant
- Basic FastAPI app
- Basic health checks
- Database migrations

### Phase 2: Indexing Pipeline

- Configure one document location
- Scan files
- Extract PDF/text content
- Chunk text
- Generate local embeddings
- Store metadata in PostgreSQL
- Store vectors in Qdrant

### Phase 3: Search and Ask

- Semantic search endpoint
- Ollama answer endpoint
- Return answer with paths, pages, and snippets
- Basic CLI or minimal web form

### Phase 4: UI

- Chat/search page
- Document catalog page
- Indexing status page
- Failed document/error page

### Phase 5: Re-indexing and Duplicates

- Scheduled scans
- Change detection
- Re-index changed files
- Tombstone deleted files
- Duplicate detection by file hash and text hash

### Phase 6: Multi-location Cataloging

- Multiple source locations
- Cross-location duplicate report
- Missing-copy report
- Optional sync planning/dry-run

## Open Decisions

- Which local Ollama model should be the default?
- Which embedding model should be the default?
- Should extracted full text be stored on disk, in PostgreSQL, or both?
- Should the UI be React or a simpler FastAPI-rendered interface?
- Should OCR be included in MVP or deferred?
- Should file synchronization be read-only reporting first, or active copy/move?

## Suggested Defaults

- LLM: `llama3.1:8b` or a newer local model that fits available RAM/VRAM
- Embeddings: `BAAI/bge-small-en-v1.5` for MVP
- Vector DB: Qdrant
- SQL DB: PostgreSQL
- API: FastAPI
- UI: React + Vite if a richer app is desired; FastAPI templates if speed matters more
- First supported files: PDF, TXT, MD
- First deployment target: local machine or NAS Docker environment

## Definition of Done for MVP

The MVP is complete when a user can:

1. Add a local/NAS folder as a source location.
2. Scan and index PDF/text documents from that folder.
3. Ask a natural-language question in a local UI.
4. Receive an Ollama-generated answer using only local retrieved context.
5. See source document paths, page numbers, and snippets.
6. Add or change a file and re-index it.
7. View indexing status and failures.
