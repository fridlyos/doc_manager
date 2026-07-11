# Phase 0 Architecture Baseline

## Status

- **Review state:** Phase 0 implementation artifact; approval required before Phase 1.
- **Recorded:** 2026-07-11.
- **Scope:** Contracts and architecture only. No application scaffolding, containers, migrations, or runtime services are included.

## Decision set

The Phase 0 decision set consists of:

1. The five proposed ADRs in [`docs/adr/`](../adr/README.md).
2. The API conventions in [`docs/api/contracts.md`](../api/contracts.md).
3. The job lifecycle in [`ingestion-job-state-machine.md`](ingestion-job-state-machine.md).
4. The deterministic synthetic corpus and expected citations in [`test-data/synthetic/`](../../test-data/synthetic/README.md).
5. The architecture and phased delivery plan in [`TECHSTACK.md`](../../TECHSTACK.md).

## Component boundaries and data ownership

| Data/capability | Authoritative owner | Derived/cache copies | Mutation boundary |
| --- | --- | --- | --- |
| Original documents | User-managed local/NAS filesystem | None | Indexer reads only; synchronization execution is out of scope |
| Source locations, paths, file observations, versions, hashes, jobs, tags, policies | PostgreSQL | API response caches only | API/service repositories in SQL transactions |
| Normalized extracted page/section text | Content-addressed artifact store | Chunk text in Qdrant payload | Ingestion worker publishes immutable artifacts |
| Chunks and processing-profile references | PostgreSQL | Qdrant point payload | Ingestion worker through catalog/vector repositories |
| Embedding vectors and semantic index | Qdrant | Rebuildable from artifacts/source documents | Vector-store adapter only |
| Local embeddings | FastEmbed worker process | Qdrant vectors | Embedding adapter only |
| Generated answer text | Selected generation provider | Not retained by default | Provider adapter through RAG service |
| Citation paths/pages | PostgreSQL plus retrieved evidence | Returned response objects | Resolved locally; never accepted from an LLM as authoritative |
| Provider secrets | Docker/environment secret injection | None | API process only; never database, UI, logs, or backups |
| Completed recovery sets | NAS backup tree | NAS-managed external copy | Maintenance backup service only |

Cross-store PostgreSQL/Qdrant operations are not distributed transactions. Handlers are idempotent, use deterministic vector IDs, and publish explicit processing states so reconciliation can repair an interruption.

## Selected deployment profile

- Windows host with a current WSL 2 kernel.
- Docker Desktop using Linux containers.
- PostgreSQL and Qdrant live data in Docker-managed Linux named volumes on local SSD.
- NAS attached to Windows as an SMB mapped drive for documents, optional immutable artifacts, and completed backups.
- Ollama is an optional native Windows generation provider.
- OpenAI is an optional external generation provider; local retrieval and embeddings remain authoritative.
- The system remains useful in search-only mode if no generation provider is ready.

## Environment observations

Read-only checks performed from the current workspace on 2026-07-11 found:

- WSL 2 Linux kernel `6.6.87.2-microsoft-standard-WSL2` on `x86_64`.
- Docker CLI/daemon are not exposed in the current shell.
- Windows executable interoperability (`wsl.exe` and `powershell.exe`) is not exposed in the current shell.
- Ollama is not exposed in the current shell.

These observations do not prove that Docker Desktop or Ollama are absent from Windows. They mean Phase 1 must verify them from the intended runtime context.

## Operator configuration still required

The following values are configuration inputs, not unresolved architecture choices:

| Input | Current value | Phase 1 gate |
| --- | --- | --- |
| Windows version/build | Not observable from this shell | Confirm supported Docker Desktop/WSL platform |
| Docker Desktop and Compose versions | Not observable | Record and pin minimum supported versions |
| Local SSD capacity for PostgreSQL/Qdrant | Not supplied | Capacity preflight succeeds |
| NAS make/model and SMB version | Not supplied | Record for operations guide |
| Stable document UNC root | Not supplied | Read-only mount and sentinel test succeeds |
| Windows drive mapping | Example `Z:` only | Post-reboot visibility test succeeds |
| NAS artifact path | Not supplied | Decide local versus optional NAS artifact storage |
| NAS completed-backup path | Not supplied | Backup write/read/delete test succeeds |
| Least-privilege NAS identity/ACLs | Not supplied | Worker is read-only; backup service has scoped write access |
| NAS external-backup destination/status signal | Not supplied | Document how completed sets leave the primary NAS |
| Recovery point objective | Provisional 24 hours | Accept or revise before backup implementation |
| Recovery time objective | Measure during first restore drill | Establish target after representative corpus sizing |
| Default generation provider | Ollama/local | May be changed to OpenAI without changing indexing |
| OpenAI model/project/key | Unconfigured | Required only when external generation is enabled |

None of these inputs changes the component boundaries. A failure to satisfy the storage or mount preflights blocks Phase 1 deployment configuration rather than causing silent fallback.

## Phase 1 start gate

Phase 1 begins only after:

- Proposed ADRs are accepted or amended.
- API and job-state contracts are reviewed.
- Synthetic ground truth is accepted as the initial test oracle.
- The operator confirms the deployment values needed for the first Compose profile.
- Any change that would move live PostgreSQL/Qdrant files onto SMB, enable external embeddings, or execute filesystem synchronization receives a new ADR.

