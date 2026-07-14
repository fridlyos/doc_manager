# Enabling external processing

External generation sends the current question plus bounded retrieved evidence
to a third-party API (OpenAI). It is **disabled by default** and requires two
independent opt-ins so it can never happen accidentally.

## The two gates

1. **Deployment gate** — start with the external override so the flag and secret
   are present only when intended:
   ```bash
   docker compose -f compose.yaml -f compose.external-llm.yaml up -d
   ```
   The override sets `DOCMAN_EXTERNAL_LLM_ENABLED=true`,
   `DOCMAN_GENERATION_PROVIDER=openai`, requires `DOCMAN_OPENAI_MODEL`, and
   mounts the API key as a Docker secret into the API service only.

2. **Source gate** — every source location has an `external_generation_policy`
   that defaults to `deny`. External generation proceeds only when **all**
   evidence-bearing sources allow it. If any selected evidence comes from an
   external-denied source, the request **fails closed** with an explanation. It
   does not silently drop evidence or switch providers.

## The API key

- Provide the key as a file on the Windows host, outside the repository, with a
  restrictive ACL. Point `DOCMAN_OPENAI_API_KEY_HOST_FILE` at it.
- Compose mounts it at `/run/secrets/openai_api_key`, readable only by the API
  container.
- The key is never written to `.env`, PostgreSQL, the browser, logs, or backups.

## What leaves the local environment

Only the question, system grounding instructions, evidence text, and opaque
citation IDs. The outbound payload excludes Windows/UNC paths, file names,
document IDs, source-location names, tags, database IDs, and original files.
Paths and document metadata are resolved locally **after** generation.

## What does not change

- There is no automatic local↔external fallback. The user selects a provider
  explicitly and retries explicitly.
- Embeddings and the vector index stay local. External **embeddings** are not
  implemented (that would send the whole corpus, not selected evidence).
- `store=false` is enforced by the adapter. It limits OpenAI-side state storage
  but does not make the request local; third-party processing and retention
  policies still apply. Review the current OpenAI API data controls before
  enabling.

## Disabling

Bring the stack back up **without** the override:

```bash
docker compose up -d
```

The secret is no longer mounted and the provider returns to local/search-only
behavior.
