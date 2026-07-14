# Generation provider configuration

The retrieval and citation pipeline is provider-neutral. Generation is optional:
with no ready provider the system runs in **search-only** mode. Two adapters
exist — local Ollama (default) and external OpenAI (opt-in).

## Local provider: native Windows Ollama

Ollama runs **natively on Windows**, not in a container. Containers reach it at
`http://host.docker.internal:11434`.

1. Install Ollama for Windows and start it (it listens on `127.0.0.1:11434`).
2. Pull the chat model referenced by `DOCMAN_OLLAMA_CHAT_MODEL`, e.g.:
   ```
   ollama pull llama3.1:8b
   ```
   Model download is a deliberate setup-time network action.
3. Leave the defaults in `.env`:
   ```
   DOCMAN_GENERATION_PROVIDER=ollama
   DOCMAN_OLLAMA_URL=http://host.docker.internal:11434
   DOCMAN_OLLAMA_CHAT_MODEL=llama3.1:8b
   ```

### Connectivity check

From the API or worker container the local provider must be reachable:

```bash
docker compose exec api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').status)"
```

`200` confirms the container can reach native Windows Ollama. `system/status`
then reports `ollama: up`. If Ollama is stopped, readiness is unaffected and the
system stays in search-only mode; it never falls back to an external provider.

Ollama is **not** a mandatory runtime dependency. When
`DOCMAN_GENERATION_PROVIDER=openai`, a missing Ollama install does not block API
readiness.

## External provider: OpenAI (opt-in)

Disabled by default. Enabling it is a deliberate deployment action requiring the
Compose override **and** per-source permission — see
[`external-processing.md`](external-processing.md). Summary of the required
configuration:

- `DOCMAN_EXTERNAL_LLM_ENABLED=true` and `DOCMAN_GENERATION_PROVIDER=openai`.
- `DOCMAN_OPENAI_MODEL=<model id>` — required; there is no assumed default.
- The API key is mounted as a Docker secret at `DOCMAN_OPENAI_API_KEY_FILE`
  (`/run/secrets/openai_api_key`), read only by the API container. It is never
  placed in `.env`, PostgreSQL, the UI, logs, or backups.

The adapter always sends stateless requests (`store=false`) and uses no hosted
tools, file search, web search, or uploads. See the OpenAI adapter section of
`TECHSTACK.md` (5.13) for the enforced request contract.

## Switching providers

Switching provider does not require re-indexing: embeddings and the vector index
are local and provider-independent. Ollama config can remain present while the
OpenAI provider is selected, and vice versa.
