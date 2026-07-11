# Technical Stack and Implementation Plan

## Document Status

- **Status:** Draft design target for review; implementation has not started.
- **Source of requirements:** [`README.md`](README.md)
- **Initial delivery target:** Single-user, local/LAN deployment using Docker Desktop with the WSL 2 Linux engine on Windows.
- **Primary constraint:** Documents, extracted text, metadata, embeddings, and vector indexes remain local. Model inference is local by default; an explicitly enabled external generation provider may receive only the current question and bounded retrieved evidence.
- **Requirement amendment:** Support both local Ollama and opt-in external LLM providers such as the OpenAI API without changing the indexing or retrieval layers.

This document converts the product plan in `README.md` into concrete technical decisions and an ordered implementation plan. It is the reference for the first implementation. Changes to important decisions should be recorded as Architecture Decision Records under `docs/adr/` once development begins.

## 1. Scope and Design Principles

The MVP will:

1. Catalog PDF and plain-text documents from one or more local, SMB, or NFS-backed directories.
2. Detect additions, content changes, moves, missing files, and restored files through repeatable scans.
3. Extract and chunk content while preserving page and source-path information.
4. Create embeddings locally and search them in Qdrant.
5. Ask a configured local or external generation provider to answer only from retrieved evidence.
6. Return stable citations containing document paths, pages, and snippets.
7. Identify exact-file and equivalent-text duplicates across locations.
8. Show search, chat, catalog, duplicate, location, job, and error views in a local web UI.
9. Compare locations and produce synchronization reports and dry-run plans without changing source files.

The implementation will follow these principles:

- **Local by default:** Ollama and local embeddings are the default; no cloud API, telemetry, CDN asset, hosted font, or remote model endpoint is enabled without explicit configuration.
- **No silent egress:** External providers require a deployment-level opt-in and source-level permission. The system never falls back from local to external inference automatically.
- **Read-only sources:** Indexing mounts and reads document roots without modifying them.
- **Catalog is authoritative:** PostgreSQL owns operational state; Qdrant owns vector search data only.
- **Idempotent jobs:** Retrying a scan or indexing job produces the same catalog and vector state.
- **Evidence before generation:** Search results and citation IDs are created before any provider generates an answer.
- **Safe synchronization:** MVP synchronization means comparison and planning, not automatic copy, move, or delete.
- **Replaceable adapters:** Extractors, embedding models, vector storage, and LLM calls sit behind small interfaces.
- **Observable failures:** A failed file is visible and retryable without failing an entire location scan.

## 2. Resolved MVP Decisions

| Decision | MVP choice | Reason |
| --- | --- | --- |
| Generation provider | Provider interface with `ollama` and `openai` adapters | Keeps retrieval provider-neutral and allows local or explicitly external inference. |
| Default provider | Ollama over its local HTTP API | Preserves the original offline/private behavior without coupling the domain layer to an Ollama SDK. |
| External provider | OpenAI Responses API, disabled by default | Provides an optional hosted model path with streaming and stateless requests. |
| Chat model | Required provider-specific configuration; no universal hard-coded model | Model availability changes independently across providers and deployments. |
| Embeddings | FastEmbed with `BAAI/bge-small-en-v1.5` | CPU-friendly local ONNX inference and a practical MVP index size. |
| Vector database | Qdrant | Durable vector search, metadata filters, Docker support, and a clear separation from the SQL catalog. |
| Catalog database | PostgreSQL | Reliable transactions, job leasing, migrations, and duplicate/location reporting. |
| Background jobs | PostgreSQL-backed queue with a dedicated worker | Avoids Redis/Celery in the MVP while preserving retries and concurrency control. |
| Scan scheduling | Database schedule plus worker polling | Works consistently for local, SMB, and NFS roots where filesystem events may be unreliable. |
| Extracted text | Compressed files under local app data; metadata and previews in PostgreSQL | Keeps large text out of SQL rows while allowing backup, re-chunking, and inspection. |
| Windows deployment profile | Docker Desktop with the WSL 2 Linux engine | Easiest Windows path for Linux containers, Docker volumes, and local development. |
| Ollama placement | Optional native Windows Ollama process, reached from containers through `host.docker.internal` | Simplifies local model management and GPU access when the local provider is enabled. |
| External fallback | Disabled | Prevents an unavailable local model from silently sending document evidence outside the local environment. |
| External embeddings | Deferred; FastEmbed remains local | External generation sends only selected evidence, while external embeddings would send the entire chunk corpus. |
| Live database storage | Docker-managed named volumes on local SSD | Keeps PostgreSQL and Qdrant off SMB/NAS live filesystems. |
| Backup target | Application-aware backup sets copied to the NAS | NAS is the recovery target, not the live database filesystem. |
| Web UI | React + TypeScript + Vite | Better fit than templates for chat streaming, filters, dashboards, and multi-screen state. |
| OCR | Deferred; failed/empty scanned PDFs are reported as `ocr_required` | Keeps the first pipeline small and makes unsupported content explicit. |
| Synchronization | Coverage reports and dry-run plans only | Prevents accidental mutation or deletion of user documents. |
| Authentication | Single-user/local-only MVP; authentication required before broader LAN use | Keeps the initial scope focused without implying that an unauthenticated service is safe to expose. |

These are defaults, not hard-coded assumptions. Provider selection, model names, paths, scan intervals, chunk settings, and resource limits will be configuration. Provider credentials are secrets and never database/UI configuration.

## 3. Technology Stack

Versions will be pinned in lockfiles and Docker image tags during Phase 1. Major runtime targets are listed here; compatible patch versions will be selected and tested together.

### 3.1 Backend and Worker

| Technology | Role |
| --- | --- |
| Python 3.12 | API, worker, scanner, extraction, chunking, and RAG orchestration |
| FastAPI | Versioned REST API, validation, OpenAPI, health endpoints, and streamed answer responses |
| Uvicorn | ASGI application server |
| Pydantic / pydantic-settings | Request/response schemas and environment-based configuration |
| SQLAlchemy 2 | PostgreSQL models, transactions, and repository layer |
| Alembic | Forward-only database migrations |
| psycopg 3 | PostgreSQL driver |
| HTTPX | Async calls to Ollama and internal health endpoints where needed |
| OpenAI Python SDK (optional dependency extra) | Typed OpenAI Responses API adapter and streaming events |
| PyMuPDF | Page-aware PDF text and metadata extraction |
| charset-normalizer | Safe encoding detection for text-like files |
| FastEmbed | Local document/query embeddings |
| qdrant-client | Collection management, upsert, filtering, deletion, and similarity search |
| tiktoken-compatible tokenizer or model tokenizer adapter | Deterministic token-aware chunk sizing; selected during the chunker implementation |
| structlog + standard logging | Structured local logs with content and secret redaction |
| tenacity | Bounded retry policies for transient database, Qdrant, and model-provider failures |
| `uv` + `pyproject.toml` | Reproducible Python dependency and tool management |

### 3.2 Data and Model Services

| Technology | Role |
| --- | --- |
| PostgreSQL 16 | Catalog, file history, schedules, jobs, tags, duplicate data, and sync plans |
| Qdrant | Embedding collections and semantic nearest-neighbor search |
| Ollama | Optional native Windows local generation provider; model installation is an explicit operator action |
| OpenAI API | Optional external generation provider; disabled until explicitly configured |
| Local/NAS filesystem mounts | Source documents and compressed extracted-text artifacts |

The selected deployment is Windows with Docker Desktop using the WSL 2 Linux engine. PostgreSQL, Qdrant, the API, worker, and UI run as Linux containers. When enabled, Ollama runs natively on Windows and is reached from containers at `http://host.docker.internal:11434`; the OpenAI adapter instead makes TLS requests from the API container to the official OpenAI API. The NAS is attached to Windows as an SMB mapped drive and is mounted into selected containers for source documents, optional extracted artifacts, and completed backups. Live PostgreSQL and Qdrant files use Docker-managed named volumes inside Docker Desktop's Linux storage on local SSD; they must not be bind-mounted to the mapped Windows/NAS drive. Section 11 defines the validated mount and backup design.

### 3.3 Frontend

| Technology | Role |
| --- | --- |
| React + TypeScript | UI application and typed component state |
| Vite | Development server and production build |
| React Router | Page routing |
| TanStack Query | API caching, mutation state, polling, and invalidation |
| Native `fetch` | JSON requests and streamed answer consumption |
| CSS modules/design tokens | Local styling without runtime CDN dependencies |
| Vitest + Testing Library | Component and UI behavior tests |
| Playwright | Browser-level end-to-end tests |

The frontend will be compiled into static assets. In production, the API container can serve those assets so the MVP does not need Nginx or a separate public UI service.

### 3.4 Quality and Operations

| Technology | Role |
| --- | --- |
| pytest + pytest-asyncio | Backend unit and async service tests |
| Testcontainers or Docker Compose test profile | PostgreSQL and Qdrant integration tests |
| Ruff | Python linting and formatting |
| mypy | Static checks at service and domain boundaries |
| ESLint + Prettier | TypeScript linting and formatting |
| Docker Compose | Local service orchestration and persistent volumes |
| Docker Desktop for Windows with WSL 2 | Selected container runtime; Linux containers and Docker-managed named volumes |
| Native Windows Ollama | Optional local model runtime; containers call `host.docker.internal:11434` when selected |
| Docker health checks | Startup ordering and operator-visible service health |
| GitHub Actions | Optional CI for lint/tests using synthetic data only; no document corpus or provider secrets |

## 4. System Architecture

```text
Browser
   |
   | HTTP / streamed response
   v
FastAPI API ---------------------------> PostgreSQL catalog
   |                                          ^
   | search / upsert / delete                 | jobs, leases,
   v                                          | metadata, paths
Qdrant vector DB                              |
   ^                                          |
   | vectors                                  |
   |                                     Worker + scheduler
   |                                          |
   |                                          v
FastEmbed <--- chunker <--- extractors <--- file scanner
                                                |
                                                v
                                  read-only local/NAS roots

FastAPI retrieval layer ---> provider policy/router ---+---> local Ollama
             |                                         |
             |                                         +---> OpenAI API (opt-in)
             |                                                    |
             +----------------------------------------------------v
                                                        grounded answer
                                                             +
                                                  citations resolved locally
                                                    from PostgreSQL evidence
```

Runtime services:

- `postgres`: authoritative catalog and durable job queue.
- `qdrant`: semantic index.
- `api`: FastAPI API and production frontend assets.
- `worker`: scanning, extraction, chunking, embedding, cleanup, and scheduled jobs.
- `ui`: Vite development service only; the production build is served by `api`.
- `backup`: on-demand/scheduled maintenance profile that creates application-aware backup sets; it is not a continuously running public service.

Optional generation providers:

- `ollama`: native Windows local model runtime, called through `host.docker.internal:11434`.
- `openai`: official external API, called over TLS only when explicitly enabled and selected.

Redis and Nginx are explicitly deferred until workload or deployment requirements justify them.

## 5. Component Responsibilities

### 5.1 Configuration Service

- Loads environment settings and database-managed settings.
- Validates that source paths are reachable by the worker.
- Tracks extraction, normalization, chunking, embedding, and generation-provider profile versions.
- Redacts secrets and sensitive content from configuration dumps and logs.
- Validates configured provider adapters and rejects arbitrary model base URLs.
- Requires a deployment-level external-processing opt-in before any remote provider can become ready.
- Loads provider credentials from environment/Docker secrets only, never PostgreSQL or the browser.

### 5.2 Source Location Manager

- Creates, updates, enables, disables, and tests source locations.
- Stores both a worker-visible `scan_root` and a user-visible `display_root`.
- Supports Linux, mapped NAS, UNC-display, and Windows-display path styles.
- Stores include extensions, exclusion globs, scan interval, and scan policy per location.
- Stores an external-generation policy per location; the default is `deny`.
- Prevents overlapping active roots by default because they can produce confusing duplicate entries.

Separating scan and display roots is required for Docker deployments. For example, the worker may read `/sources/contracts/a.pdf` while the UI should show `\\nas\legal\contracts\a.pdf`.

### 5.3 Scanner and Reconciler

- Recursively enumerates supported files without following symlink loops.
- Applies exclusion rules before opening files.
- Captures relative path, size, modification time, and stable file identity where available.
- Uses size/mtime as a fast change signal and SHA-256 as the content authority.
- Reconciles scan observations with the current catalog in one location-scoped workflow.
- Emits jobs for new or changed content and catalog updates for moves, missing files, and restored files.
- Does not mark unseen files missing when a scan is incomplete or the root is unavailable.

Filesystem watchers are not the source of truth. A later watcher can request an early scan, but periodic reconciliation remains authoritative.

### 5.4 Fingerprinting and Duplicate Service

- Streams file SHA-256 calculation to avoid loading large files into memory.
- Normalizes extracted text and computes a separate text SHA-256.
- Reports **exact duplicates** when active file versions share a file hash.
- Reports **text duplicates** when different file hashes share normalized extracted text.
- Groups all active paths and source locations for each duplicate.
- Reuses extraction, chunks, and embeddings for content already represented by an identical normalized-text object.
- Produces source-location coverage and missing-copy reports.

Duplicate groups are derived from authoritative hashes. They can be materialized for UI performance but must be safely rebuildable.

### 5.5 Extractor Registry

The extractor interface returns page/section records, document metadata, warnings, and an extractor version.

MVP extractors:

- PDF: PyMuPDF, preserving one-based page numbers.
- TXT, Markdown, log: decoded text with synthetic section boundaries.
- CSV: row-aware text representation with headers repeated as needed.

Deferred adapters:

- DOCX, XLSX, and PPTX.
- Image and scanned-PDF OCR.
- Email and archive formats.

Encrypted, malformed, empty, or OCR-only files receive a specific error code and remain visible in the error queue.

### 5.6 Extracted Artifact Store

- Writes compressed, versioned extraction artifacts under `app-data/extracted-text/`.
- Uses a content-addressed path based on normalized text hash and extraction version.
- Writes to a temporary file and atomically renames only after success.
- Stores page/section boundaries so re-chunking does not require reopening the source file.
- Supports cleanup only after confirming no catalog record references an artifact.

Original documents remain in their source locations and are never copied into the application data directory by indexing.

### 5.7 Chunker

- Chunks extracted content using a deterministic, token-aware algorithm.
- Starts with a target of 750 tokens and 100 tokens of overlap, both configurable.
- Preserves page ranges and avoids crossing page boundaries unless a page is too small.
- Records chunk index, token count, text hash, and chunking profile version.
- Generates deterministic chunk and Qdrant point IDs from content identity plus profile identity.
- Keeps chunks small enough that several citations fit inside the selected generation model's context window.

### 5.8 Embedding Service

- Loads the configured FastEmbed model once per worker process.
- Separates document and query embedding calls so model-specific prefixes can be applied correctly.
- Batches document chunks within configurable memory limits.
- Records model name, revision, vector size, distance metric, and preprocessing profile.
- Refuses to mix incompatible vectors in one Qdrant collection.

An embedding-profile change creates a new collection or a controlled rebuild. It never silently writes incompatible vectors into the active collection.

### 5.9 Qdrant Repository

- Creates and validates the collection for an embedding profile.
- Upserts deterministic points idempotently.
- Stores only retrieval payload required for search: content ID, chunk ID, page range, text, and profile identifiers.
- Applies source, extension, tag, and status filters supplied by the retrieval layer.
- Deletes/tombstones points only when their canonical content is no longer referenced or a profile is retired.
- Exposes consistency checks comparing SQL chunk records with vector points.

Paths are resolved from PostgreSQL at query time instead of being treated as permanent Qdrant payload. This prevents stale citations after a move.

### 5.10 Catalog Repository

- Owns SQL transactions and prevents database code from leaking into domain services.
- Manages source locations, physical entries, content versions, chunks, jobs, errors, tags, and sync plans.
- Uses explicit row locking for job claims and scan reconciliation.
- Maintains timestamps in UTC while returning localizable ISO 8601 values to the UI.
- Preserves history through state transitions rather than hard-deleting catalog entries.

### 5.11 Job Queue, Worker, and Scheduler

- Stores durable jobs in PostgreSQL.
- Claims work using `FOR UPDATE SKIP LOCKED` and a lease expiration time.
- Records attempts, progress, heartbeats, structured errors, and cancellation requests.
- Retries transient failures with bounded exponential backoff.
- Sends permanent extraction failures to the visible error queue.
- Enforces one active scan per source location.
- Enqueues scheduled scans without duplicating an already queued/running scan.

Initial job types:

- `scan_location`
- `index_file`
- `remove_stale_vectors`
- `reindex_document`
- `reindex_all_for_profile`
- `build_duplicate_report`
- `build_sync_plan`
- `catalog_consistency_check`

### 5.12 Retrieval Service

- Validates and embeds the query locally.
- Searches Qdrant with optional metadata filters.
- Applies a configurable score threshold.
- Collapses repeated/overlapping chunks and limits repeated evidence from one content object.
- Resolves all currently active paths and display paths through PostgreSQL.
- Returns search results independently of generation-provider availability.
- Builds a context set within a token budget for the selected chat model.

The search API remains useful when Ollama is stopped and no external provider is configured: users can still retrieve ranked snippets and paths.

### 5.13 Generation Provider and RAG Service

The retrieval and citation pipeline depends on an internal `GenerationProvider` interface rather than Ollama/OpenAI response types. Each adapter implements:

- `provider_id` and capabilities.
- Readiness/model validation.
- Provider-specific context/output limits.
- Stateless streamed text generation.
- Normalized usage, finish, refusal, timeout, rate-limit, and error events.
- Cancellation and bounded retry behavior.

Shared RAG responsibilities:

- Give the provider numbered evidence blocks with server-generated opaque citation IDs.
- Instruct the model to use only supplied evidence and report insufficient evidence.
- Map citation IDs to paths/pages locally; never trust or display a provider-generated filesystem path.
- Record provider/model, timing, request ID, and token usage where available without recording prompt or document content.
- Keep question/answer history disabled unless an explicit retention policy enables it.
- Treat prompt-injection text inside documents as untrusted evidence, not system instructions.

#### Provider policy and privacy modes

| Mode | Retrieval/embeddings | Generation | External data transfer |
| --- | --- | --- | --- |
| `local` | Local FastEmbed + Qdrant | Ollama | None |
| `hybrid_external` | Local FastEmbed + Qdrant | OpenAI | Current question plus selected evidence text only |
| `external_indexing` | External embeddings | External or local | Whole chunk corpus during indexing; deferred/not implemented |

External generation proceeds only when:

1. `DOCMAN_EXTERNAL_LLM_ENABLED=true`.
2. The named adapter is on the deployment allowlist and has a valid secret.
3. Every evidence-bearing source location allows external generation.
4. The request explicitly selects or accepts the configured external provider.

The outbound payload contains the question, system grounding instructions, evidence text, and opaque citation IDs. It excludes Windows/UNC paths, file names, document IDs, source-location names, tags, database IDs, and original files. If any selected evidence is external-denied, the request fails closed with an explanation; it does not silently discard evidence or switch providers.

There is no automatic local-to-external or external-to-local fallback. The user retries with another provider explicitly.

#### Ollama adapter

- Calls the configured native Windows Ollama endpoint.
- Keeps all prompt and answer content on the local host.
- Supports the same normalized streaming/citation contract as external adapters.

#### OpenAI adapter

- Uses the official Python SDK and the Responses API, which OpenAI recommends for new projects. [OpenAI Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- Streams typed Responses API events into the provider-neutral answer stream. [OpenAI streaming guide](https://developers.openai.com/api/docs/guides/streaming-responses)
- Sends stateless requests with `store=false`; it does not use `previous_response_id`, Conversations, background mode, hosted file search, web search, tools, or file uploads.
- Requires an operator-configured model ID; the application does not assume one permanent OpenAI model alias.
- Reads the API key from a Docker secret/environment injection available only to the API service. Official guidance says keys should not be committed or exposed in application code. [OpenAI production guidance](https://developers.openai.com/api/docs/guides/production-best-practices)
- Surfaces provider rate limits, timeouts, refusal, and authentication failures without retrying unsafe/non-idempotent behavior indefinitely.

`store=false` limits Responses API state storage, but it does not make an external request local or override every provider retention/control policy. OpenAI states that API inputs/outputs are not used to train models by default unless the customer opts in; operators must still review the current API data controls and their own compliance requirements before enabling the adapter. [OpenAI API data controls](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint), [OpenAI business data policy](https://openai.com/policies/how-your-data-is-used-to-improve-model-performance/)

### 5.14 Synchronization Planner

- Compares selected source locations by relative path, file hash, and normalized text hash.
- Reports exact matches, renamed equivalents, missing copies, and conflicts.
- Produces a persisted dry-run plan with proposed source and target paths.
- Never executes file operations in the MVP.

Any future executor must be a separate, disabled-by-default component with allowlisted roots, conflict rules, checksums after copy, an audit trail, and explicit confirmation. Automatic delete remains out of scope.

### 5.15 Web API

- Uses `/api/v1` routes and typed request/response models.
- Returns stable machine-readable error codes plus safe user messages.
- Supports pagination, sorting, filtering, and cancellation for long-running work.
- Provides readiness information for PostgreSQL, Qdrant, the local embedding model, source mounts, and each enabled generation provider.
- Does not expose raw filesystem reads through arbitrary user-supplied paths.

### 5.16 Web UI

Planned pages:

- **Search:** semantic query, filters, ranked snippets, pages, all known paths, and copy-path action.
- **Ask:** streamed answer with provider/model and `Local`/`External` data-boundary badges, numbered evidence cards, and an insufficient-evidence state.
- **Catalog:** paginated documents, status, type, location, modified time, hashes, and tags.
- **Document detail:** all paths/versions, extraction information, chunks, errors, and re-index action.
- **Duplicates:** exact and text-equivalent groups with cross-location coverage.
- **Locations:** create/edit/test locations, inclusion rules, schedules, scan actions, and per-source external-generation permission.
- **Jobs:** live/polled progress, attempts, failures, retry, and cancellation.
- **Errors:** actionable extraction and indexing failures.
- **System status:** local service/model health and active profile information.
- **Provider status:** enabled adapters, model readiness, external-processing policy, and credential presence (never credential values).
- **Sync comparison:** location coverage and dry-run plan; no execute control in the MVP.

Browsers generally cannot safely open arbitrary local/NAS paths. The guaranteed MVP action is copy path. A platform-specific reveal/open helper may be added later only with explicit allowlisted-path security.

## 6. Data Model

The README draft is refined to separate a physical file path from canonical extracted content. That separation is necessary to reuse vectors for duplicates and to keep citations correct when files move.

### `source_locations`

- `id`, `name`
- `scan_root`, `display_root`, `path_style`
- `enabled`, `read_only`
- `external_generation_policy` (`deny` by default or `allow`)
- `include_extensions`, `exclude_globs`
- `scan_interval_minutes`, `last_successful_scan_at`
- `created_at`, `updated_at`

### `catalog_entries`

Represents one relative path in one source location.

- `id`, `source_location_id`, `relative_path`
- `file_name`, `extension`, `mime_type`
- `state` (`discovered`, `queued`, `indexed`, `failed`, `missing`, `unsupported`)
- `current_file_version_id`
- `first_seen_at`, `last_seen_at`, `missing_since`
- `created_at`, `updated_at`
- unique constraint: `(source_location_id, relative_path)`

### `file_versions`

Represents the observed bytes of a catalog entry at a point in time.

- `id`, `catalog_entry_id`
- `size_bytes`, `mtime`, `sha256`
- `content_object_id` (nullable until extraction completes)
- `observed_at`, `indexed_at`
- `extraction_status`, `error_code`, `error_message`

### `content_objects`

Represents reusable normalized extracted content.

- `id`, `text_hash`
- `extractor_name`, `extractor_version`, `normalization_version`
- `artifact_path`, `page_count`, `character_count`
- `metadata_json`, `created_at`
- unique identity across text hash and relevant pipeline profile

### `chunks`

- `id`, `content_object_id`, `chunk_index`
- `page_start`, `page_end`
- `text_hash`, `text_preview`, `token_count`
- `chunking_profile_id`, `qdrant_point_id`
- `created_at`, `updated_at`

### `processing_profiles`

- `id`, `profile_type` (`extraction`, `chunking`, `embedding`, `chat`)
- `name`, `settings_json`, `profile_hash`
- `active`, `created_at`

Chat profile settings may include provider ID, model ID, context/output limits, and provider-specific non-secret options. Secrets never enter `settings_json`.

### `ingestion_jobs`

- `id`, `job_type`, `status`, `priority`
- `source_location_id`, `catalog_entry_id`
- `payload_json`, `progress_current`, `progress_total`
- `attempt_count`, `max_attempts`
- `lease_owner`, `lease_expires_at`, `heartbeat_at`
- `requested_at`, `started_at`, `finished_at`
- `error_code`, `error_message`, `cancel_requested_at`

### `job_events`

- `id`, `job_id`, `level`, `event_type`, `message`, `details_json`, `created_at`

### `duplicate_groups` and `duplicate_group_members`

- Group type (`file_hash` or `text_hash`) and hash value.
- Members point to current file versions/catalog entries.
- Rebuildable cache used for fast UI queries.

### `sync_plans` and `sync_plan_items`

- Compared source/target locations and plan status.
- Proposed action (`copy`, `conflict`, `already_present`, `manual_review`).
- Source/target relative paths, hashes, reason, and timestamps.
- No execution columns or automatic action in the MVP.

### Optional MVP-support tables

- `tags`, `catalog_entry_tags`
- `saved_searches`
- `system_settings`

Chat/search history is disabled by default and should not receive a table until retention and privacy behavior are explicitly designed.

## 7. Core Data Flows

### 7.1 Initial and Scheduled Scan

```text
schedule/manual request
  -> enqueue one location scan
  -> validate root availability
  -> enumerate and record observations
  -> compare with catalog snapshot
  -> hash new/suspected-changed files
  -> recognize moves/copies by SHA-256 when possible
  -> enqueue indexing only for new content
  -> mark missing entries only after a complete scan
  -> refresh duplicate and coverage reports
```

### 7.2 Index a File

```text
claim job
  -> verify file still matches observed fingerprint
  -> calculate SHA-256
  -> reuse an exact known version when safe
  -> extract pages/sections
  -> normalize text and calculate text hash
  -> reuse canonical content when already indexed
  -> otherwise write extraction artifact
  -> create deterministic chunks
  -> batch local embeddings
  -> upsert Qdrant points
  -> commit SQL references/status
```

If a file changes during processing, the job exits without publishing mixed state and queues a fresh observation.

### 7.3 Re-index Decision

Re-indexing occurs when any of the following changes:

- File SHA-256.
- Extraction or normalization profile.
- Chunking profile.
- Embedding profile/model.
- An operator explicitly requests a forced re-index.

Path-only moves and mtime-only changes with identical SHA-256 update catalog metadata without repeating extraction or embedding.

### 7.4 Search

```text
query + filters
  -> local query embedding
  -> Qdrant similarity search
  -> score threshold and overlap deduplication
  -> resolve current catalog paths in PostgreSQL
  -> return ranked snippets, pages, scores, and paths
```

### 7.5 Ask

```text
question + filters
  -> retrieval flow
  -> bounded evidence set with citation IDs
  -> provider policy and source-level egress check
  -> selected Ollama or OpenAI adapter
  -> grounded generation request
  -> stream answer
  -> return server-resolved citation objects
```

An empty or weak evidence set returns an explicit insufficient-evidence response instead of asking the model to guess. External mode sends only the bounded evidence after policy approval; paths and document metadata are resolved locally after generation.

### 7.6 Delete and Restore

- A complete scan marks an unseen path `missing`; it does not delete its history.
- A vector is retained while any active catalog entry references its content object.
- Unreferenced vectors/artifacts become cleanup candidates after a configurable grace period.
- A restored identical file reconnects to existing content without re-embedding.

## 8. API Surface

All routes are prefixed with `/api/v1` except process-level health endpoints.

### Health and system

```text
GET  /health/live
GET  /health/ready
GET  /api/v1/system/status
GET  /api/v1/system/profiles
GET  /api/v1/system/providers
POST /api/v1/system/providers/{id}/test
```

### Locations and scanning

```text
GET    /api/v1/locations
POST   /api/v1/locations
GET    /api/v1/locations/{id}
PATCH  /api/v1/locations/{id}
POST   /api/v1/locations/{id}/test
POST   /api/v1/locations/{id}/scan
POST   /api/v1/locations/{id}/disable
```

### Catalog and duplicates

```text
GET   /api/v1/documents
GET   /api/v1/documents/{id}
POST  /api/v1/documents/{id}/reindex
GET   /api/v1/duplicates
GET   /api/v1/duplicates/{id}
GET   /api/v1/coverage
```

### Search and RAG

```text
POST  /api/v1/search
POST  /api/v1/ask
POST  /api/v1/ask/stream
```

Ask requests accept an enabled `provider_id`. Responses identify the actual provider/model and whether external evidence transfer occurred. A provider test uses fixed synthetic text and never sends document content.

### Jobs and errors

```text
GET   /api/v1/jobs
GET   /api/v1/jobs/{id}
POST  /api/v1/jobs/{id}/retry
POST  /api/v1/jobs/{id}/cancel
GET   /api/v1/errors
```

### Synchronization planning

```text
POST  /api/v1/sync-plans
GET   /api/v1/sync-plans/{id}
GET   /api/v1/sync-plans/{id}/items
```

There is intentionally no sync execution endpoint in the MVP.

## 9. Planned Project Structure

```text
doc_manager/
├── README.md
├── TECHSTACK.md
├── .env.example
├── .gitignore
├── compose.yaml
├── compose.external-llm.yaml
├── Makefile
├── backend/
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── alembic.ini
│   ├── migrations/
│   │   └── versions/
│   ├── src/
│   │   └── doc_manager/
│   │       ├── main.py
│   │       ├── api/
│   │       │   ├── dependencies.py
│   │       │   ├── errors.py
│   │       │   └── v1/
│   │       │       ├── router.py
│   │       │       └── routes/
│   │       ├── core/
│   │       │   ├── config.py
│   │       │   ├── logging.py
│   │       │   └── security.py
│   │       ├── domain/
│   │       │   ├── enums.py
│   │       │   ├── models.py
│   │       │   └── errors.py
│   │       ├── db/
│   │       │   ├── session.py
│   │       │   ├── models/
│   │       │   └── repositories/
│   │       ├── ingestion/
│   │       │   ├── scanner.py
│   │       │   ├── reconciler.py
│   │       │   ├── fingerprint.py
│   │       │   ├── normalizer.py
│   │       │   ├── chunker.py
│   │       │   └── extractors/
│   │       ├── embeddings/
│   │       │   ├── base.py
│   │       │   └── fastembed.py
│   │       ├── vector_store/
│   │       │   ├── base.py
│   │       │   └── qdrant.py
│   │       ├── rag/
│   │       │   ├── retrieval.py
│   │       │   ├── context.py
│   │       │   ├── prompts.py
│   │       │   ├── provider_policy.py
│   │       │   └── providers/
│   │       │       ├── base.py
│   │       │       ├── registry.py
│   │       │       ├── ollama.py
│   │       │       └── openai.py
│   │       ├── services/
│   │       │   ├── catalog.py
│   │       │   ├── duplicates.py
│   │       │   ├── locations.py
│   │       │   └── sync_planner.py
│   │       ├── jobs/
│   │       │   ├── queue.py
│   │       │   ├── scheduler.py
│   │       │   ├── worker.py
│   │       │   └── handlers/
│   │       └── artifact_store/
│   │           └── extracted_text.py
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── contract/
│       └── fixtures/
├── frontend/
│   ├── package.json
│   ├── pnpm-lock.yaml
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   ├── src/
│   │   ├── app/
│   │   ├── api/
│   │   ├── components/
│   │   ├── features/
│   │   │   ├── ask/
│   │   │   ├── search/
│   │   │   ├── catalog/
│   │   │   ├── duplicates/
│   │   │   ├── locations/
│   │   │   ├── jobs/
│   │   │   └── sync-plans/
│   │   ├── pages/
│   │   ├── styles/
│   │   └── test/
│   └── e2e/
├── deploy/
│   ├── api.Dockerfile
│   ├── worker.Dockerfile
│   └── frontend.Dockerfile
├── docs/
│   ├── adr/
│   ├── api/
│   ├── operations/
│   │   ├── backup-restore.md
│   │   ├── provider-configuration.md
│   │   └── external-processing.md
│   └── threat-model.md
├── scripts/
│   ├── bootstrap.sh
│   ├── check.sh
│   ├── backup.sh
│   ├── restore.sh
│   └── verify-backup.sh
└── test-data/
    └── synthetic/          # generated/non-sensitive fixtures only
```

The backend uses a single package shared by API and worker containers. Separate containers provide failure isolation without duplicating domain logic.

## 10. Configuration Plan

Environment variables configure deployment-level values; user-manageable source locations and schedules live in PostgreSQL.

Planned variables:

```text
DOCMAN_ENV=development|production|test
DOCMAN_BIND_HOST=127.0.0.1
DOCMAN_PORT=8000
DOCMAN_DATABASE_URL=postgresql+psycopg://...
DOCMAN_QDRANT_URL=http://qdrant:6333
DOCMAN_QDRANT_COLLECTION=doc_chunks
DOCMAN_GENERATION_PROVIDER=ollama
DOCMAN_EXTERNAL_LLM_ENABLED=false
DOCMAN_EXTERNAL_PROVIDER_ALLOWLIST=openai
DOCMAN_EXTERNAL_SOURCE_DEFAULT=deny
DOCMAN_EXTERNAL_MAX_EVIDENCE_TOKENS=12000
DOCMAN_EXTERNAL_MAX_OUTPUT_TOKENS=2000
DOCMAN_EXTERNAL_REQUEST_TIMEOUT_SECONDS=90
DOCMAN_OLLAMA_URL=http://host.docker.internal:11434
DOCMAN_OLLAMA_CHAT_MODEL=llama3.1:8b
DOCMAN_OPENAI_MODEL=<required when OpenAI is enabled>
DOCMAN_OPENAI_API_KEY_FILE=/run/secrets/openai_api_key
DOCMAN_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
DOCMAN_ARTIFACT_ROOT=/app-data/extracted-text
DOCMAN_ALLOWED_SOURCE_ROOTS=/sources
DOCMAN_NAS_DOCUMENTS_HOST_PATH=Z:/Documents
DOCMAN_NAS_ARTIFACTS_HOST_PATH=Z:/DocManager/artifacts
DOCMAN_NAS_BACKUPS_HOST_PATH=Z:/DocManager/backups
DOCMAN_NAS_MOUNT_SENTINEL=.docman-source-id
DOCMAN_BACKUP_ROOT=/backups
DOCMAN_BACKUP_RETENTION_DAILY=14
DOCMAN_BACKUP_RETENTION_WEEKLY=8
DOCMAN_BACKUP_RETENTION_MONTHLY=12
DOCMAN_LOG_LEVEL=INFO
DOCMAN_WORKER_CONCURRENCY=1
DOCMAN_JOB_LEASE_SECONDS=300
DOCMAN_CHUNK_TARGET_TOKENS=750
DOCMAN_CHUNK_OVERLAP_TOKENS=100
DOCMAN_SEARCH_TOP_K=12
DOCMAN_SEARCH_SCORE_THRESHOLD=<calibrated value>
DOCMAN_STORE_QUERY_HISTORY=false
```

`.env.example` will contain safe placeholders only. Real `.env` files, source mounts, database credentials, models, indexes, artifacts, and logs must be ignored by Git.

The OpenAI key is not written to `.env`, PostgreSQL, frontend state, logs, or backups. Compose mounts it as a secret only into the API service. The OpenAI adapter always sets `store=false`; this is an enforced behavior, not a user-editable environment setting. Ollama configuration can remain present while disabled so switching providers does not require re-indexing.

Ollama is not a mandatory runtime dependency. When `DOCMAN_GENERATION_PROVIDER=openai`, missing Ollama installation/model health does not block API readiness. When no generation provider is ready, the application enters search-only mode instead of failing indexing or semantic search.

The external-provider Compose override follows this shape:

```yaml
services:
  api:
    environment:
      DOCMAN_EXTERNAL_LLM_ENABLED: "true"
      DOCMAN_GENERATION_PROVIDER: openai
    secrets:
      - openai_api_key

secrets:
  openai_api_key:
    file: ${DOCMAN_OPENAI_API_KEY_HOST_FILE}
```

`DOCMAN_OPENAI_API_KEY_HOST_FILE` identifies an ACL-protected Windows file outside the repository. The override is required in addition to source-level permissions, making external processing a deliberate deployment action.

### Docker source-mount constraint

A running container cannot dynamically gain access to an arbitrary Windows or NAS directory selected in the UI. The mapped NAS drive must be connected before Compose starts, and its allowed parent directories are explicitly bind-mounted into the required containers:

```yaml
services:
  worker:
    volumes:
      - type: bind
        source: ${DOCMAN_NAS_DOCUMENTS_HOST_PATH}
        target: /sources/nas
        read_only: true
        bind:
          create_host_path: false

  backup:
    volumes:
      - type: bind
        source: ${DOCMAN_NAS_BACKUPS_HOST_PATH}
        target: /backups
        bind:
          create_host_path: false
```

The Windows `.env` example uses forward-slash paths such as `Z:/Documents`. `create_host_path: false` prevents Compose from silently creating an empty local directory when a configured path is absent. Startup also requires a known sentinel file at the source root and a write/read/delete probe in the backup root.

Windows drive letters are scoped to a user/logon context, so Docker Desktop may not see a mapping created under a different account or non-interactive session. The installation test must prove that the exact Docker Desktop/Compose context can read the drive and that it remains available after Windows reboot. The source location stores a stable UNC `display_root`, such as `\\nas\documents`, even if Compose scans through `Z:/Documents`.

The UI can create locations only at or below `/sources/nas`; it cannot add another Windows drive dynamically. Adding another drive requires updating `.env`/Compose and recreating the affected container. If Docker Desktop cannot consume the mapped drive reliably, a direct read-only CIFS Docker volume is a possible document-mount fallback, but its credentials and Docker-metadata exposure require a separate security review. It still must not be used for live PostgreSQL or Qdrant data.

## 11. Local Storage and Backup Layout

### 11.1 Storage layout

```text
Docker Desktop managed volumes
├── postgres_data/
├── qdrant_data/
└── backup_staging/

Windows host storage
└── Ollama native model cache           # optional; managed by the Windows Ollama install

Windows mapped NAS drive
├── Documents/
└── DocManager/
    ├── artifacts/                 # optional; immutable/checksummed
    └── backups/
        ├── incoming/              # incomplete; never restorable
        ├── completed/             # immutable completed backup sets
        └── restore-test-results/
```

The live database directories and completed backup directory serve different purposes:

- `postgres_data/` and `qdrant_data/` are Docker-managed Linux volumes containing frequently changing live database files.
- `backup_staging/` holds temporary dumps/snapshots locally so a NAS interruption cannot corrupt a completed backup set.
- `artifacts/` contains immutable, content-addressed extraction artifacts when NAS artifact storage is enabled.
- `backups/incoming/<backup-id>/` is incomplete and never counted as a valid backup.
- `backups/completed/<backup-id>/` contains immutable application-aware backup artifacts, checksums, and a completion manifest.

Source documents remain outside application backup scope unless an operator separately manages them.

### 11.2 Validated live-storage profiles

| Physical storage presented to the container | PostgreSQL live data | Qdrant live data | Project support |
| --- | --- | --- | --- |
| Docker-managed named volume inside Docker Desktop's WSL 2 Linux storage | Supported | Supported and preferred | **Selected Windows profile** |
| Windows-local NTFS path shared into a Linux container | Not selected for `PGDATA` | Qdrant warns against Windows/WSL shared mounts | Shared source/config files only |
| Windows mapped NAS drive using SMB/CIFS | **Rejected** | **Rejected/risk of data loss** | Documents, immutable artifacts, and completed backups only |
| Direct CIFS Docker volume | **Rejected** | **Rejected** | Optional document mount after credential review |
| NAS iSCSI LUN mounted by a dedicated Linux host/VM and formatted with a POSIX filesystem | Supported | Supported by Qdrant's block-storage requirement | Alternative platform, not the selected Docker Desktop profile |
| Object storage | Unsupported for live data | Unsupported for live data | Backup artifacts only |

Important consequences:

- Giving a mapped SMB/CIFS path a Docker volume name does not change its underlying filesystem semantics. It remains unsupported for live Qdrant data.
- PostgreSQL and Qdrant named volumes remain inside Docker Desktop's Linux storage even though Docker is controlled from Windows.
- Do not relocate Docker Desktop's WSL disk image or named-volume backing store onto the mapped NAS drive as a workaround.
- Only one PostgreSQL server may own a `PGDATA` directory, and each Qdrant node must have its own storage directory.
- Qdrant's startup filesystem compatibility check is a required deployment preflight, not a warning to bypass.
- PostgreSQL 16 page checksums will be enabled at cluster initialization to improve detection of storage corruption. Checksums detect damage; they do not replace backups.
- Storage and container image versions must be pinned before the first production data is created.

### 11.3 Selected deployment: Windows Docker Desktop with mapped NAS drive

The Windows drive mapping is an SMB file share. It is suitable for the document corpus and backup artifacts, but not for the frequently changing live database files.

```text
Windows / Docker Desktop
├── Docker named volume: postgres_data  -> live PostgreSQL
├── Docker named volume: qdrant_data    -> live Qdrant
├── Docker named volume: backup_staging -> temporary snapshot/dump work
├── Native Windows Ollama               -> optional local generation provider/cache
└── Windows mapped NAS drive Z:
    ├── Documents                       -> worker /sources/nas (read-only)
    └── DocManager
        ├── artifacts                   -> worker artifact store (optional)
        └── backups                     -> backup /backups (read-write)
```

Illustrative Compose configuration:

```yaml
services:
  postgres:
    volumes:
      - postgres_data:/var/lib/postgresql/data

  qdrant:
    volumes:
      - qdrant_data:/qdrant/storage

  worker:
    volumes:
      - type: bind
        source: ${DOCMAN_NAS_DOCUMENTS_HOST_PATH}
        target: /sources/nas
        read_only: true
        bind:
          create_host_path: false
      - type: bind
        source: ${DOCMAN_NAS_ARTIFACTS_HOST_PATH}
        target: /app-data/extracted-text
        bind:
          create_host_path: false

  backup:
    volumes:
      - backup_staging:/staging
      - type: bind
        source: ${DOCMAN_NAS_BACKUPS_HOST_PATH}
        target: /backups
        bind:
          create_host_path: false

volumes:
  postgres_data:
  qdrant_data:
  backup_staging:
```

Deployment rules:

- Docker Desktop uses the current WSL 2 Linux engine and Linux containers.
- PostgreSQL, Qdrant, and backup staging use Docker-managed named volumes on local SSD.
- When the local provider is enabled, Ollama runs natively on Windows and containers reach it through `host.docker.internal:11434`.
- The NAS document tree is read-only in the worker.
- The writable backup path exists only in the maintenance backup container.
- Extracted artifacts may live on the NAS because they are immutable and checksummed; keeping them in a local named volume remains the faster option.
- Backup generation and Qdrant snapshot temporary work happen locally, followed by a verified copy to a `.partial` directory on the NAS. The completion marker is written only after the NAS copy and checksum verification succeed.
- The catalog stores a stable UNC `display_root` for returned paths even when the worker scans through a drive-letter bind mount.

#### Mapped-drive availability and failure handling

Mapped drive letters exist within a Windows user/logon context. Before any scan or backup, the service preflight verifies:

1. Docker Desktop can see the configured path from the same context that runs Compose.
2. A source sentinel contains the expected location ID.
3. The source mount is read-only inside the worker.
4. The backup mount passes a write/read/delete probe.
5. The observed UNC server/share identity matches configuration.

If the drive is disconnected or credentials expire:

- The worker marks the location `unavailable`, not empty.
- No documents are tombstoned and no vectors are deleted.
- The scan fails visibly and retries with bounded backoff.
- A backup remains incomplete in local staging and is not counted toward retention.
- The UI reports the Windows path, expected UNC path, and last successful access time.

The installation and post-reboot acceptance tests must prove the mapped drive remains visible to Docker Desktop. If drive-letter binding is unreliable, the fallback is a direct read-only CIFS Docker volume for documents and a separate CIFS backup volume. That fallback uses a least-privilege NAS account and requires a credential-exposure review because Docker volume options may be visible in Docker metadata.

#### Hard constraint: live databases cannot use the mapped drive

The following mounts are prohibited:

```yaml
# Prohibited: Z: is an SMB/Windows shared filesystem.
postgres:
  volumes:
    - Z:/DocManager/postgres:/var/lib/postgresql/data

qdrant:
  volumes:
    - Z:/DocManager/qdrant:/qdrant/storage
```

If physically NAS-resident live PostgreSQL and Qdrant files become non-negotiable, the runtime architecture must change from Docker Desktop bind mounts to a dedicated Linux host/VM with an exclusively mounted iSCSI LUN. Attaching iSCSI to Windows and then sharing the resulting Windows drive into Docker Desktop is not treated as equivalent block/POSIX access for Qdrant.

### 11.4 Backup authority and recovery model

The data has an explicit authority order:

1. PostgreSQL is authoritative for locations, catalog state, hashes, jobs, profiles, chunk metadata, and Qdrant point IDs.
2. Source documents are authoritative for original content.
3. Extracted-text artifacts are immutable derived data that make re-chunking/re-embedding faster.
4. Qdrant is a rebuildable semantic index, not the only copy of catalog information.

This means a missing or incompatible Qdrant snapshot does not prevent recovery. PostgreSQL plus reachable source documents or extracted-text artifacts can rebuild the vector collection. A Qdrant snapshot primarily reduces recovery time.

### 11.5 Canonical application-aware backup set

The MVP backup coordinator will create one versioned backup set as follows:

1. Create a unique `backup_id` in the local Docker `backup_staging` volume with sufficient free space.
2. Acquire a database advisory maintenance lock and stop claiming new indexing/catalog-mutation jobs.
3. Allow active mutation jobs to finish or cancel safely; search and read-only catalog use may continue.
4. Create an online PostgreSQL custom-format logical dump with `pg_dump` and a globals dump with `pg_dumpall --globals-only`.
5. Request a native Qdrant collection snapshot through the Qdrant API and download the completed archive. Collection aliases and the exact active collection/profile mapping are recorded separately because collection snapshots do not include aliases.
6. Capture or incrementally copy immutable extracted-text artifacts referenced by the PostgreSQL dump.
7. Export non-secret deployment configuration, schema revision, source scan/display mappings, and model/profile metadata.
8. Generate SHA-256 checksums and a machine-readable manifest.
9. Verify every local artifact, copy the set to NAS `backups/incoming/<backup-id>/`, verify the NAS-side checksums, rename it to `backups/completed/<backup-id>/` where the share supports an atomic same-share rename, and write the completion marker last.
10. Release the maintenance lock and resume worker mutation jobs.
11. Let the NAS backup system copy only completed, immutable backup directories to its external destination.

The backup manifest records at least:

```text
backup_id
started_at_utc
completed_at_utc
application_git_revision
database_schema_revision
postgresql_server_version
postgres_dump_filename + sha256
postgres_globals_filename + sha256
qdrant_exact_version
qdrant_collection_name
qdrant_snapshot_filename + sha256
embedding_profile_hash
chunking_profile_hash
generation_provider + model_id + generation_profile_hash
artifact_inventory + checksums
non_secret_configuration_checksum
backup_format_version
```

PostgreSQL's logical dump is transactionally consistent by itself. Briefly pausing cross-store mutations makes the PostgreSQL catalog and Qdrant snapshot easier to reconcile. Restore always runs the catalog/vector consistency checker because the vector index is intentionally treated as rebuildable.

### 11.6 Backup schedule, retention, RPO, and RTO

Initial defaults:

| Backup | Default schedule | Retention | Purpose |
| --- | --- | --- | --- |
| PostgreSQL custom-format logical dump + globals | Nightly | 14 daily, 8 weekly, 12 monthly | Portable authoritative catalog recovery |
| Qdrant collection snapshot | Nightly and before upgrades/large rebuilds | Same backup-set retention | Fast semantic-index recovery |
| Extracted-text artifact increment | Nightly | Retained while referenced by a retained catalog backup | Avoid repeat extraction |
| Non-secret configuration and backup manifest | Every backup set | Same as set | Reproducible restore |
| NAS external copy | After completion marker appears | NAS policy, never shorter than application retention | Independent failure-domain copy |

MVP targets:

- **RPO:** Up to 24 hours with nightly backups.
- **RTO:** Restore PostgreSQL and artifacts first; restore Qdrant when version-compatible or rebuild it in the background. Actual time depends on corpus and index size and will be measured during restore tests.

Retention is configurable. A backup is not considered independent while it exists only on the same NAS and storage pool as the live data. The intended minimum is:

1. Live data.
2. Completed backup set on the NAS.
3. Encrypted external NAS-managed copy on a separate device or location.

The external copy can remain entirely under the operator's control; no cloud service is required.

### 11.7 Optional PostgreSQL point-in-time recovery tier

Sites requiring an RPO below 24 hours may enable a second PostgreSQL physical-backup tier:

- Periodic full `pg_basebackup` backups.
- Continuous WAL archiving to a dedicated protected NAS backup path.
- Backup manifests verified with `pg_verifybackup`.
- Alerts for failed/stalled WAL archiving, abnormal archive growth, and insufficient space.
- Retention that guarantees an uninterrupted WAL sequence from every retained base backup through its recovery window.

This tier supports PostgreSQL point-in-time recovery but is more operationally complex. Logical dumps remain useful for portable and selective recovery. WAL archives contain all database changes and receive the same access protection as the database itself.

### 11.8 NAS snapshots and raw Docker-volume copies

The NAS's automatic filesystem backup of a live database directory is not, by itself, the canonical backup:

- PostgreSQL documents that an ordinary file copy requires the server to be shut down. A live storage snapshot is usable only when it is a trustworthy, atomic, consistent snapshot covering the entire data directory, WAL, and every tablespace.
- Docker's generic volume-tar procedure copies files but does not quiesce PostgreSQL or Qdrant and therefore does not make a changing database application-consistent.
- Qdrant provides a native snapshot API; that snapshot archive is the supported portable backup unit.

A NAS block/filesystem snapshot may be kept as a fast secondary recovery layer if one of these is true:

1. PostgreSQL and Qdrant containers are cleanly stopped before the snapshot; or
2. The NAS has a documented application-consistent quiesce/freeze integration that is tested for the complete set of volumes.

Even then, native PostgreSQL dumps/base backups and Qdrant snapshots remain required because they are portable, verifiable, and independently restorable.

Never scrape Docker-managed volume internals under `/var/lib/docker` while services are running. Docker treats direct manipulation of those internals as unsupported; use database APIs, an explicitly mounted maintenance volume, or a stopped cold-volume workflow.

### 11.9 Qdrant snapshot constraints

- Use native collection snapshots for the MVP single-node deployment.
- Record and pin the exact Qdrant version used to create each snapshot.
- Restore with a compatible Qdrant minor/patch version as required by Qdrant's snapshot rules; the safest restore target is the exact pinned image.
- Collection snapshots contain collection configuration, points, payloads, and indexes but not collection aliases; aliases are restored from the manifest/catalog configuration.
- Leave approximately twice the collection size free during restore because the snapshot and restored collection coexist temporarily.
- Keep snapshot temporary work on compatible local/block storage. A completed snapshot can then be downloaded to the NAS backup directory.
- If a snapshot is unavailable or incompatible, create a fresh collection from PostgreSQL/artifacts and switch the active collection only after consistency checks pass.

### 11.10 Restore procedure

Every restore is performed into new/empty volumes first:

1. Select a completed backup set and verify its completion marker, manifest, and all checksums.
2. Deploy the recorded PostgreSQL and Qdrant image versions without starting application mutation workers.
3. Restore PostgreSQL globals and the application database into a new cluster.
4. Restore extracted-text artifacts and non-secret configuration; provide secrets separately.
5. Restore the Qdrant snapshot into a new collection, or schedule a full vector rebuild if the snapshot is missing/incompatible.
6. Restore collection aliases/profile mapping only after collection validation.
7. Run database migrations only as a separately logged upgrade step after the original backup has been proven restorable.
8. Run catalog/artifact/Qdrant consistency checks and repair missing points or remove orphan points.
9. Validate health endpoints, known synthetic searches, document paths, duplicate reports, and one grounded Ask request.
10. Enable API traffic and mutation workers only after validation succeeds.

Restores never overwrite the only existing live volume. Rollback remains possible until the restored deployment passes acceptance checks.

### 11.11 Backup verification and monitoring

Each run verifies:

- Non-zero files, expected inventory, SHA-256 checksums, and free-space thresholds.
- PostgreSQL dump catalog readability; physical backups additionally run `pg_verifybackup`.
- Qdrant snapshot metadata, exact source version, and collection name.
- Artifact inventory references against the backed-up catalog.
- Successful external-copy status from the NAS where that status is available.

Operational requirements:

- Alert on failed, incomplete, or overdue backups and low backup/storage capacity.
- Never prune the last verified backup.
- Perform an automated disposable restore test where practical and a documented full restore drill at least quarterly.
- Record restore duration and use it to refine RTO expectations.
- Test recovery after every PostgreSQL/Qdrant major upgrade or backup-format change.

### 11.12 Official storage and backup references

- [PostgreSQL 16 filesystem and NFS requirements](https://www.postgresql.org/docs/16/creating-cluster.html)
- [PostgreSQL 16 SQL dump backups](https://www.postgresql.org/docs/16/backup-dump.html)
- [PostgreSQL 16 file-level and consistent-snapshot constraints](https://www.postgresql.org/docs/16/backup-file.html)
- [PostgreSQL 16 `pg_basebackup`](https://www.postgresql.org/docs/16/app-pgbasebackup.html)
- [PostgreSQL 16 WAL archiving and point-in-time recovery](https://www.postgresql.org/docs/16/continuous-archiving.html)
- [PostgreSQL 16 backup verification](https://www.postgresql.org/docs/16/app-pgverifybackup.html)
- [PostgreSQL 16 data checksums](https://www.postgresql.org/docs/16/checksums.html)
- [Qdrant live-storage requirements](https://qdrant.tech/documentation/installation/#storage)
- [Qdrant incompatible-filesystem warnings](https://qdrant.tech/documentation/guides/common-errors/#incompatible-file-system)
- [Qdrant snapshots and restore constraints](https://qdrant.tech/documentation/operations/snapshots/)
- [Docker volume semantics and generic backup behavior](https://docs.docker.com/engine/storage/volumes/)
- [Docker-managed volume access constraints](https://docs.docker.com/engine/storage/#volume-mounts)
- [Docker Desktop WSL 2 backend on Windows](https://docs.docker.com/desktop/features/wsl/)
- [Docker Desktop WSL 2 storage and bind-mount guidance](https://docs.docker.com/desktop/features/wsl/best-practices/)
- [Docker Compose bind mounts and `create_host_path`](https://docs.docker.com/reference/compose-file/services/#volumes)

The implementation must revalidate these references when dependency major versions or the selected NAS/container platform changes.

## 12. Security and Privacy Plan

- Bind the default deployment to `127.0.0.1`; LAN binding is an explicit configuration change.
- Do not publish PostgreSQL or Qdrant ports outside the Compose network by default.
- Keep native Windows Ollama bound to localhost by default when enabled; containers reach it through `host.docker.internal`.
- Keep external providers disabled by default and show a persistent `External provider` warning whenever one is active.
- Require both deployment-level enablement and per-source external-generation permission; new locations default to `deny`.
- Never fall back to an external provider automatically when Ollama is unavailable.
- Send external providers only the question, selected evidence text, opaque citation IDs, and grounding instructions; keep paths and catalog metadata local.
- Do not enable external embeddings, file uploads, hosted file search, web search, or provider tools in the MVP.
- Mount external API credentials only into the API container through Docker secrets; never expose credentials to the browser, worker, database, logs, backups, or diagnostics.
- Restrict external provider adapters to reviewed HTTPS endpoints; arbitrary user-supplied base URLs are not accepted.
- Enforce stateless OpenAI requests with `store=false` and document that third-party processing/retention policies still apply.
- Mount source directories read-only.
- Mount the writable NAS backup destination only into the maintenance backup service, not into the API, worker, PostgreSQL, or Qdrant services.
- Treat PostgreSQL globals, WAL archives, extracted text, Qdrant snapshots, and manifests as sensitive data; restrict permissions and encrypt external backup media.
- Resolve and validate paths beneath an allowlisted source root before reading any file.
- Reject traversal, symlink escape, device-file, and arbitrary-path requests.
- Set maximum file size, extraction time, page count, and decompression limits.
- Treat document text as untrusted input and separate it clearly from system instructions.
- Avoid logging document bodies, embeddings, prompts, database URLs, or credentials.
- Sanitize error details returned to the browser while retaining safe diagnostic codes.
- Disable analytics and externally hosted frontend assets. Cloud model providers are opt-in and governed by the external-processing policy above.
- Add authentication, authorization, CSRF review, and TLS before exposing the service to multiple LAN users.
- Document model download as a deliberate setup-time network action. `local` mode can operate offline; `hybrid_external` mode requires outbound internet access and is not local-only.

## 13. Testing Strategy

### Unit tests

- Path normalization and scan/display-root mapping.
- Storage-profile validation, including rejection of NFS/CIFS-backed Qdrant storage.
- Windows drive-letter/UNC mapping and NAS sentinel identity validation.
- Include/exclude and symlink rules.
- Streaming hashes and normalized text hashes.
- Added/changed/moved/missing/restored reconciliation.
- Deterministic chunk boundaries and IDs.
- Duplicate grouping and source coverage.
- Job claim, lease, retry, and cancellation rules.
- Retrieval deduplication and context budgeting.
- Citation ID mapping and insufficient-evidence behavior.
- Provider selection, no-fallback behavior, and deployment/source-level external-policy denial.
- External payload construction proving paths, file names, tags, and catalog IDs are excluded.
- Sync-plan comparison with no filesystem mutation.
- Backup manifest state transitions, completion markers, checksum inventory, and retention rules.

### Integration tests

- Alembic migration from an empty PostgreSQL database.
- Job leasing with competing workers.
- Qdrant collection creation, idempotent upsert, search, filter, and cleanup.
- Qdrant native snapshot download and exact-version restore into a temporary collection.
- PostgreSQL logical dump and restore into a new temporary database.
- FastEmbed document/query vector dimensions.
- PDF/text extraction with synthetic fixtures.
- Ollama and OpenAI adapter behavior through deterministic HTTP stubs using one shared provider contract.
- OpenAI request assertions for `store=false`, no hosted tools/files, configured model, streaming events, and secret redaction.
- API error contracts, pagination, filters, and streamed responses.
- Interrupted source/backup mount behavior: no false deletion and no completed backup marker.

### End-to-end tests

Using temporary source roots and synthetic documents:

1. Add two locations.
2. Scan and index PDF/TXT/MD files.
3. Search and verify paths/pages/snippets.
4. Ask through a deterministic model stub and verify citations.
5. Add, change, rename, copy, delete, and restore files.
6. Re-scan and verify catalog/vector state.
7. Verify exact and text duplicate groups.
8. Build a sync dry-run and verify that no source file changed.
9. Exercise the main UI workflows in Playwright.
10. Create a completed backup set, restore into empty volumes, and verify catalog/vector consistency.
11. Disconnect the test source during a scan and verify the location becomes unavailable without tombstoning documents.
12. Run Ask in `local` and stubbed `hybrid_external` modes, then verify an external-denied source fails closed without a provider call.

### Quality gates

A phase is complete only when its migrations, unit tests, integration tests, linting, type checks, operational notes, and failure behavior are included. Automated tests must not require private documents, real provider credentials, or cloud services. An optional manually invoked live-provider smoke test uses synthetic text only.

## 14. Implementation Phases

### Phase 0: Contracts and Architecture

Deliverables:

- Approve this plan and unresolved deployment constraints.
- Add initial ADRs for physical-file/canonical-content separation, PostgreSQL job queue, extracted-text storage, and generation-provider/privacy boundaries.
- Record the selected platform: Windows, current WSL 2, Docker Desktop Linux containers, Docker-managed live database volumes, and SMB mapped NAS paths for documents/artifacts/backups.
- Record the NAS platform, stable UNC paths, Windows drive mapping, least-privilege account, external-backup destination, and initial RPO/RTO.
- Define API error envelope, pagination format, IDs, timestamps, and job state machine.
- Define sample synthetic documents and expected citations.

Exit criteria:

- Component boundaries and data ownership are agreed.
- No open decision can force a major rewrite of Phase 1 infrastructure.

### Phase 1: Repository and Local Infrastructure

Deliverables:

- Create backend/frontend structure and lockfiles.
- Add Compose services for PostgreSQL, Qdrant, API, worker, UI development, and the backup maintenance profile.
- Document native Windows Ollama setup and the `host.docker.internal:11434` connectivity check.
- Add the on-demand backup maintenance profile, `.env.example`, health checks, local volumes, source-mount examples, and safe defaults.
- Add an external-provider Compose override and Docker-secret wiring without committing a key or enabling external inference by default.
- Document and validate the supported Docker Desktop/WSL versions and Linux-container mode.
- Add Windows mapped-drive bind mounts with `create_host_path: false`, read-only document access, stable UNC display mapping, and least-privilege backup access.
- Enable PostgreSQL page checksums during first cluster initialization.
- Add storage preflight checks and document that Qdrant filesystem failures cannot be overridden for production.
- Fail the relevant scan/backup preflight when the expected NAS mount identity or sentinel is absent; never reconcile documents against an accidentally empty local path.
- Implement FastAPI liveness/readiness and structured logging.
- Configure lint, format, type-check, and test commands.

Exit criteria:

- One documented command starts the local stack.
- Health status distinguishes required services from optional/unready local and external generation providers.
- PostgreSQL and Qdrant use Docker-managed named volumes and pass their durability/filesystem checks.
- Document and backup mapped drives remain readable/writable as intended after a Windows reboot and Docker Desktop restart.
- No database or model service is internet-exposed by default.

### Phase 2: Catalog, Locations, and Durable Jobs

Deliverables:

- Implement initial Alembic schema and repositories.
- Implement location CRUD/test API and scan/display path mapping.
- Implement PostgreSQL job claim, lease, heartbeat, retry, cancel, and events.
- Implement worker and periodic scheduler.
- Add catalog/location/job UI foundations.

Exit criteria:

- A location can be configured, tested, and scanned into catalog observations.
- Restarting API/worker does not lose queued work.
- An unreachable root does not falsely mark documents missing.

### Phase 3: Scanner, Extraction, and Reconciliation

Deliverables:

- Implement safe traversal, filtering, hashing, and reconciliation.
- Implement PDF, TXT, MD, CSV, and log extractors.
- Implement versioned normalization and compressed artifact storage.
- Handle add/change/move/missing/restore states.
- Add document detail, errors, and manual retry/re-index API/UI.

Exit criteria:

- Synthetic filesystem lifecycle tests pass.
- Page numbers survive PDF extraction.
- Errors are isolated per document and visible to the user.

### Phase 4: Chunking, Embeddings, and Vector Search

Deliverables:

- Implement deterministic page-aware chunking.
- Implement FastEmbed adapter and embedding-profile validation.
- Implement Qdrant collection lifecycle and idempotent point operations.
- Implement `/search` with filters, thresholds, snippets, pages, and current paths.
- Add search UI and vector/catalog consistency check.

Exit criteria:

- Repeated indexing creates no duplicate chunks or vector points.
- A known query retrieves expected synthetic evidence.
- Search remains functional without any generation provider.

### Phase 5: Pluggable Local/External RAG Generation

Deliverables:

- Implement the normalized generation-provider interface, registry, health checks, streaming events, cancellation, usage metadata, and bounded timeouts.
- Implement the Ollama adapter as the default local provider.
- Implement deployment/source-level external-processing policy with `deny` as the default and no automatic fallback.
- Implement the OpenAI Responses API adapter using the official SDK, `store=false`, stateless streaming, no hosted tools/files, and Docker-secret authentication.
- Implement provider-neutral evidence selection, grounded prompts, and server-owned citation mapping.
- Implement insufficient-evidence, provider-unavailable, authentication, rate-limit, external-policy-denied, and refusal states.
- Add Ask UI with provider selection, streamed answer/evidence cards, a persistent Local/External badge, and an external-data preview/confirmation.
- Add provider contract tests and a synthetic optional OpenAI live smoke test.

Exit criteria:

- Answers use the retrieved evidence and expose paths/pages/snippets.
- A model cannot invent a clickable citation path.
- Local mode works with outbound provider access disabled and contacts no cloud service.
- External mode sends only explicitly allowed question/evidence text and never paths, file names, original files, or denied-source content.
- An unavailable provider produces an explicit error and never triggers a different provider automatically.
- OpenAI and Ollama pass the same grounding/citation contract fixtures.

### Phase 6: Scheduled Re-indexing and Duplicates

Deliverables:

- Enable per-location schedules and manual file/location/all re-indexing.
- Implement profile-driven full re-index jobs.
- Implement exact-file and normalized-text duplicate reports.
- Reuse canonical content and vectors across duplicate paths.
- Add duplicate and coverage UI.

Exit criteria:

- Add/change/move/delete/restore changes converge after a scan.
- Duplicate groups show every active location/path.
- Model/profile changes produce an explicit controlled rebuild.

### Phase 7: Multi-location Comparison and Sync Planning

Deliverables:

- Implement pairwise location coverage reports.
- Implement relative-path/hash/text comparison rules.
- Persist and display dry-run sync plans and conflicts.
- Document how a future separately reviewed executor could consume a plan.

Exit criteria:

- Users can identify matching, missing, renamed, and conflicting content.
- Integration and E2E tests prove the feature never writes to source roots.

### Phase 8: Hardening and MVP Release

Deliverables:

- Add resource limits, graceful shutdown, stale-lease recovery, and cleanup grace periods.
- Run threat-model review for filesystem access and document prompt injection.
- Implement coordinated PostgreSQL dump, Qdrant snapshot, artifact inventory, checksums, atomic completion, and retention in the backup maintenance profile.
- Add backup/restore, optional PostgreSQL PITR, upgrade/migration, model setup, and troubleshooting guides.
- Add provider enablement, key rotation, external-data review, rate-limit/cost-control, and incident-disable procedures.
- Run threat-model tests for provider secret leakage, accidental egress, prompt injection, and path/metadata disclosure.
- Connect the completed backup directory to the NAS external-backup workflow without exposing live Docker volume internals.
- Add performance measurements on a representative local corpus.
- Complete accessibility and browser workflow review.

Exit criteria:

- All MVP definition-of-done items in `README.md` pass end to end.
- Backup/restore, NAS external-copy detection, and fresh-install procedures are tested.
- A full restore into empty volumes passes catalog/artifact/vector consistency and known-query checks.
- Known limits and deferred features are documented.

## 15. Delivery Order and Dependency Map

```text
Infrastructure
    |
    v
SQL schema + durable jobs
    |
    v
locations -> scanner -> extraction -> artifacts
                              |
                              v
                         chunking -> embeddings -> Qdrant
                                                   |
                                                   v
UI/API catalog <------------------------------- search
                                                   |
                                                   v
                                        generation provider RAG
                                                   |
                                                   v
                             schedules + duplicates + coverage
                                                   |
                                                   v
                                         sync dry-run planning
```

Each vertical feature should include schema, backend service, API, UI state, tests, and documentation together. This avoids completing all backend work before discovering UI or API contract gaps.

## 16. Operational Targets for the MVP

These are validation targets, not guarantees for every NAS or model:

- Indexing is resumable after API, worker, or host restart.
- Normal scans avoid re-extracting unchanged SHA-256 content.
- File hashing and extraction are streaming/bounded rather than whole-file memory loads.
- UI list endpoints are paginated and remain usable with at least 100,000 catalog entries.
- Search returns its first non-generated response promptly on local hardware after models are warm.
- Long indexing and generation-provider operations expose progress or an explicit working state.
- Every answer citation resolves to at least one current path or is labeled historical/missing.

Exact latency and throughput budgets will be measured after hardware, corpus size, average file size, and model constraints are known.

## 17. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| NAS disconnect looks like mass deletion | Mark files missing only after a complete successful scan. |
| Browser cannot open local/UNC paths | Guarantee copy-path; defer a tightly scoped native helper. |
| Duplicate paths create duplicate vectors | Separate physical entries from canonical content objects and use deterministic IDs. |
| Embedding model changes vector shape/meaning | Version profiles and use a new/rebuilt collection. |
| Scanned PDFs return no text | Report `ocr_required`; add OCR as a later adapter. |
| Large/corrupt files exhaust resources | File/page/time limits, streaming hashes, worker isolation, and clear error states. |
| A generation provider hallucinates sources | Server creates evidence IDs and resolves paths independently of model output. |
| Sensitive evidence is sent to an external provider accidentally | Require global opt-in plus per-source allow, display an External badge/payload summary, exclude metadata, and fail closed. |
| Local provider outage silently causes cloud fallback | Prohibit automatic provider fallback; require an explicit retry/provider selection. |
| External API key leaks | Mount it only as an API-container secret, redact configuration/errors, and document immediate rotation. |
| External costs, rate limits, or model behavior change | Configure model/limits explicitly, capture non-content usage metadata, bound retries/output, and run provider contract/evaluation fixtures. |
| Document prompt injection | Treat documents as quoted evidence and keep system instructions outside context blocks. |
| Worker dies while holding work | Expiring leases, heartbeat, idempotent handlers, and retry limits. |
| PostgreSQL/Qdrant live storage is placed on the mapped NAS drive | Reject the deployment; require Docker Desktop-managed Linux named volumes. |
| Windows mapped drive is invisible to Docker Desktop or credentials expire | Validate sentinel/UNC identity before each scan, preserve catalog state, and expose an unavailable status with bounded retry. |
| NAS automatic backup copies changing live database files | Treat it as non-canonical; protect completed PostgreSQL/Qdrant-native backup sets instead. |
| Docker Desktop local VM/named volumes are lost | Restore PostgreSQL and Qdrant snapshots from the NAS, then run consistency repair/re-indexing. |
| SMB scanning/hash throughput is slow | Use scheduled reconciliation, size/mtime fast checks, bounded hashing concurrency, and benchmark against the real NAS. |
| Active synchronization damages files | No execution capability in the MVP; all plans are read-only dry runs. |
| Local-only service is accidentally exposed | Loopback bind, internal-only service ports, and documented auth/TLS gate for LAN use. |

## 18. Deferred Features

- Active copy/move/delete synchronization.
- OCR and image indexing.
- Office document extraction.
- Hybrid lexical/vector search and reranking.
- Multi-user authentication, permissions, and per-source authorization.
- Additional external LLM adapters beyond OpenAI.
- External embedding providers or hosted vector/file-search services.
- Native desktop path-open/reveal integration.
- Redis/Celery or distributed workers.
- Automatic tags, summaries, and entity extraction.
- Mobile application.

Deferred features must not leave misleading or partially active controls in the MVP UI.

## 19. Requirements Traceability

| README requirement | Planned implementation |
| --- | --- |
| Ask questions about documents | Provider-neutral retrieval/RAG with Ollama and opt-in OpenAI adapters plus Ask UI |
| Provide document paths | Scan/display root mapping, current-path SQL resolution, evidence cards, and copy-path action |
| Keep information local by default | `local` mode keeps embeddings, retrieval, prompts, and generation local; `hybrid_external` is explicit and transfers only allowed question/evidence text |
| Re-index added/changed files | Scheduled/manual reconciliation, durable jobs, content/profile fingerprints |
| Multiple locations | Source location manager, separate roots, location-scoped scans and filters |
| Find duplicates | File SHA-256 and normalized-text SHA-256 groups across physical catalog entries |
| Synchronize/catalog locations | Coverage reports and persisted read-only dry-run plans |
| Use a vector database | Qdrant with profile-safe collections and deterministic point IDs |
| Provide a UI | React pages for Ask, Search, Catalog, Duplicates, Locations, Jobs, Errors, Status, and Sync Plans |

## 20. MVP Completion Checklist

- [ ] A user can add and validate at least two local/NAS source locations.
- [ ] Complete scans safely reconcile added, changed, moved, missing, and restored files.
- [ ] PDF, TXT, MD, CSV, and log files can be extracted and indexed locally.
- [ ] Search returns useful snippets, page numbers where available, and all current display paths.
- [ ] Ask returns a grounded answer through the selected Ollama or OpenAI adapter with server-resolved citations.
- [ ] Search still works when every generation provider is unavailable.
- [ ] Local mode performs no external model request and remains the default.
- [ ] OpenAI remains disabled without global opt-in, a mounted secret, a configured model, and source-level permission.
- [ ] External requests set `store=false`, use no hosted tools/files, and omit paths, filenames, tags, and catalog IDs.
- [ ] Provider failure never causes an automatic local/external fallback.
- [ ] Per-file, per-location, and profile-wide re-indexing are available.
- [ ] Jobs, progress, retries, and extraction failures are visible in the UI.
- [ ] Exact-file and equivalent-text duplicates are visible across locations.
- [ ] Location coverage and sync dry-run reports never mutate source files.
- [ ] Docker Desktop uses Linux containers with PostgreSQL and Qdrant on Docker-managed named volumes, never the mapped NAS drive.
- [ ] The mapped document and backup paths pass sentinel, permission, disconnect, and post-Windows-reboot tests.
- [ ] A coordinated PostgreSQL, Qdrant, extracted-artifact, and configuration backup is written as an immutable completed set.
- [ ] The NAS automatically copies completed backup sets—not uncoordinated live database files—to independent external storage.
- [ ] A restore into new/empty volumes passes checksum, catalog, vector, path, and known-query validation.
- [ ] The default deployment exposes no database/model service to the internet and contacts no cloud service at runtime; external mode contacts only explicitly enabled provider endpoints.
