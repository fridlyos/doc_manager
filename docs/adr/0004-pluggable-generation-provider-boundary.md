# ADR 0004: Use a Pluggable Generation-Provider Privacy Boundary

- **Status:** Proposed
- **Date:** 2026-07-11
- **Decision owners:** Project maintainers

## Context

The system must support fully local Ollama generation and optional external models such as OpenAI without coupling indexing, retrieval, citations, or UI streaming to one vendor. External generation changes the privacy boundary because the current question and retrieved document text leave the local environment.

## Decision

Define a provider-neutral `GenerationProvider` contract with built-in `ollama` and `openai` adapters. Retrieval, embeddings, Qdrant, path resolution, and citation authority remain local under both adapters.

### Provider contract

Each provider normalizes:

- Provider/model identity and readiness.
- Context and output limits.
- Streamed text, completion, refusal, usage, timeout, rate-limit, authentication, cancellation, and error events.
- A stateless request containing grounding instructions, the question, opaque evidence IDs, and bounded evidence text.

Provider SDK response objects never cross the adapter boundary.

### Privacy modes

- `local`: FastEmbed/Qdrant retrieval plus Ollama generation; no model-request egress.
- `hybrid_external`: local retrieval plus explicitly enabled OpenAI generation; sends only the current question and selected evidence text.
- `external_indexing`: would send the chunk corpus to an external embedding provider; deferred and requires a new ADR.

### External processing gates

Every external request requires all of:

1. Deployment-level external LLM enablement.
2. An allowlisted provider adapter and mounted API secret.
3. A configured provider model.
4. `allow` on every source location represented in the evidence.
5. Explicit selection/acceptance of the external provider for the Ask request.

New sources default to `deny`. Mixed allowed/denied evidence fails closed; the service neither drops denied evidence silently nor switches providers.

There is no automatic provider fallback in either direction.

### Outbound payload

Allowed:

- Current question.
- Grounding/system instructions.
- Bounded selected chunk text.
- Opaque per-request citation IDs.

Prohibited:

- Windows/UNC paths and filenames.
- Source-location names, tags, catalog/content/chunk IDs, and hashes.
- Original file uploads.
- Hosted file search, web search, tools, background mode, or provider-managed conversation state.

The application resolves citations and paths locally after generation.

### OpenAI adapter

- Use the official Python SDK and Responses API. OpenAI recommends Responses for new projects: <https://developers.openai.com/api/docs/guides/migrate-to-responses>.
- Use typed streaming events: <https://developers.openai.com/api/docs/guides/streaming-responses>.
- Enforce `store=false` and stateless requests.
- Load the API key only from an API-container Docker secret. OpenAI recommends environment/secret management rather than committing keys: <https://developers.openai.com/api/docs/guides/production-best-practices>.
- Do not hard-code a supposedly permanent external model alias; the operator configures and tests a model ID.
- Treat external provider retention/data-control policy as an operator compliance decision even with `store=false`: <https://platform.openai.com/docs/models/default-usage-policies-by-endpoint>.

### Retention and audit

- Question/answer content is not stored locally by default.
- Non-content operational metadata may include provider ID, model ID, duration, provider request ID, outcome, and token usage.
- Secrets, prompts, evidence, and answers are excluded from logs, backups, and provider-health responses.

## Consequences

### Positive

- Ollama can be absent when OpenAI is selected.
- Indexing and search work with no generation provider.
- Adding another reviewed provider does not change retrieval/citation contracts.
- External data transfer is visible, narrow, and fail-closed.

### Negative

- Provider adapters and normalized streaming errors require contract tests.
- External mode is not fully local and depends on network, provider policy, quotas, and cost.
- Per-source permissions and user-facing boundary indicators add UX/schema work.

## Alternatives considered

- **Call Ollama directly throughout the RAG service:** simplest local implementation but vendor-coupled.
- **Use a generic OpenAI-compatible base URL:** convenient but makes capabilities/security ambiguous and permits arbitrary egress endpoints.
- **Automatically fall back to OpenAI:** improves availability but violates explicit privacy expectations.
- **Upload documents to hosted vector/file search:** duplicates the corpus externally and abandons the local index boundary.

