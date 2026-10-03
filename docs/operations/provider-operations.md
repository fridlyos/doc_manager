# Provider operations

Day-2 operations for the generation providers: enabling one, rotating the OpenAI
key, auditing what external processing actually sends, bounding cost, and
disabling external generation in an incident. For first-time setup see
[`provider-configuration.md`](provider-configuration.md) and the two opt-in gates
in [`external-processing.md`](external-processing.md); this guide is about running
and changing a provider after it is configured.

## Enable a provider

- **Local (Ollama), default.** Nothing to enable server-side: with a reachable
  Ollama and a pulled model the system generates locally; otherwise it stays in
  search-only mode. See [`model-setup.md`](model-setup.md).
- **External (OpenAI), opt-in.** Two independent gates, by design — a deployment
  gate and a per-source gate — so it can never happen by accident. Bring the stack
  up with the external override and set the required config:
  ```bash
  docker compose -f compose.yaml -f compose.external-llm.yaml up -d
  ```
  This sets `DOCMAN_EXTERNAL_LLM_ENABLED=true`, `DOCMAN_GENERATION_PROVIDER=openai`,
  requires `DOCMAN_OPENAI_MODEL`, and mounts the key as a Docker secret into the
  **API service only**. Then each source location's `external_generation_policy`
  must be `allow` for its evidence to be eligible (fails closed otherwise).

## Key rotation (OpenAI)

The key is a Docker secret file on the host, mounted read-only into the API
container at `/run/secrets/openai_api_key` and read only by
`Settings.read_openai_api_key()`. It is never in `.env`, PostgreSQL, the browser,
logs, or backups. To rotate:

1. Write the new key to the host secret file
   (`DOCMAN_OPENAI_API_KEY_HOST_FILE`), keeping a restrictive ACL.
2. Recreate the API service so the new secret is mounted:
   ```bash
   docker compose -f compose.yaml -f compose.external-llm.yaml up -d --force-recreate api
   ```
   The key is read per request (never cached), so a recreated API picks up the new
   value on its next Ask. The worker/DB/browser never held the old key.
3. Revoke the old key at the provider.

No re-index or data migration is needed — rotation touches only the API secret.

## External-data review (what the boundary attests)

Every Ask result carries a `data_boundary` report (contract §8.2). It is the
auditable record of what left the machine:

- `classification` — `local` or `external`.
- `external_request_attempted` / `external_transfer_occurred` — flip to true once
  the outbound HTTP write begins.
- `external_payload` — counts only: `question_sent`, `grounding_instructions_sent`,
  `evidence_blocks_sent`, `evidence_characters_sent`, `opaque_citation_ids_sent`.
- The metadata counters `paths_sent`, `file_names_sent`, `tags_sent`,
  `catalog_ids_sent`, `original_files_sent` are **structurally always zero** — no
  code path sets them (enforced by `test_threat_model` / `test_external_policy`).

To audit egress: a `local` classification means nothing left; an `external` one
sent only the question, grounding text, bounded evidence text, and opaque `E#`
aliases — never paths, file names, tags, catalog ids, or original files (resolved
locally after generation). The OpenAI adapter sends `store=false`, no hosted
tools, and no file/web search.

## Rate-limit and cost control

Generation cost is bounded by configuration, not left open-ended. Tune in `.env`:

| Setting | Default | Bounds |
| --- | --- | --- |
| `DOCMAN_EXTERNAL_MAX_EVIDENCE_TOKENS` | 12000 | Max evidence tokens sent to OpenAI per request |
| `DOCMAN_EXTERNAL_MAX_OUTPUT_TOKENS` | 2000 | Max tokens OpenAI may generate |
| `DOCMAN_EXTERNAL_REQUEST_TIMEOUT_SECONDS` | 90 | Per-request external timeout |
| `DOCMAN_GENERATION_MAX_OUTPUT_TOKENS` | 1200 | Local (Ollama) output cap |
| `DOCMAN_GENERATION_REQUEST_TIMEOUT_SECONDS` | 120 | Local per-request timeout |
| `DOCMAN_ASK_MAX_EVIDENCE_BLOCKS` | 12 | Max evidence blocks selected per Ask |
| `DOCMAN_ASK_MAX_CHUNKS_PER_CONTENT` | 3 | Cap repeated evidence from one document |

The evidence token budget is derived at request time from the provider's context
window (`DOCMAN_OPENAI_CONTEXT_TOKENS` / the local model's advertised context)
minus the output reservation, then further capped by the limits above. Lower the
evidence/output caps to reduce per-request external spend; there is no background
or batch external call — external processing happens only on an explicit Ask.

## Incident disable (one flag)

To stop all external generation immediately, bring the stack up **without** the
external override:

```bash
docker compose up -d
```

The secret is no longer mounted and `DOCMAN_EXTERNAL_LLM_ENABLED` is false, so the
transfer gate in `evaluate_external_policy` fails closed — external Ask returns a
denied/explained result and no outbound request is attempted. **Local Ask and
search keep working** (and search works even with no provider at all). Equivalent
effect without a full redeploy: set `DOCMAN_EXTERNAL_LLM_ENABLED=false` (and/or
remove the provider from the allowlist) and recreate the API service. Re-enable by
restoring the override — no re-index, the index is provider-independent.

## Related

- [`provider-configuration.md`](provider-configuration.md) — first-time setup.
- [`external-processing.md`](external-processing.md) — the two opt-in gates.
- [`model-setup.md`](model-setup.md) — pulling/caching models.
- [`troubleshooting.md`](troubleshooting.md) — provider failures.
- `docs/security/threat-model.md` — T-PI-4 egress controls + boundary counters.
