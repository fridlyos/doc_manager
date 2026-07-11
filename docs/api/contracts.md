# API Contract

## Status and scope

- **Status:** Phase 0 normative contract for the first implementation.
- **Applies to:** the HTTP API under `/api/v1`, including the provider-neutral Ask stream.
- **Does not define:** database schemas, provider SDK types, UI component types, or arbitrary filesystem access.
- **Source architecture:** `TECHSTACK.md`.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** describe implementation requirements. Examples are illustrative, but field names, enum spellings, media types, and stated behavior are normative.

## 1. HTTP and route conventions

### 1.1 Base paths and media types

- Application routes start with `/api/v1` and use plural, lowercase, hyphenated resource names.
- Process probes remain unversioned at `/health/live` and `/health/ready`. They are deployment probes, not a stable application-data API.
- Canonical routes have no trailing slash. Clients MUST use the canonical form and MUST NOT depend on framework redirect behavior.
- JSON request bodies use `Content-Type: application/json; charset=utf-8`, except partial resource updates, which use `application/merge-patch+json`.
- Successful JSON responses use `application/json; charset=utf-8`.
- Errors use `application/problem+json; charset=utf-8`.
- Ask streaming responses use `text/event-stream; charset=utf-8`.
- Requests and responses are UTF-8 only. `NaN`, positive infinity, and negative infinity are invalid JSON values.

The API version is repeated in successful response metadata as `api_version: "1"` and in the `Docman-Api-Version: 1` response header. Authentication is outside the single-user MVP, but adding it does not change the resource contract.

### 1.2 Methods and status codes

| Operation | Expected result |
| --- | --- |
| Read a resource or collection | `200 OK` |
| Create a resource synchronously | `201 Created` with `Location` |
| Accept durable background work | `202 Accepted` with job `Location` |
| Successful request with deliberately no body | `204 No Content` |
| Conditional GET not modified | `304 Not Modified` |
| Invalid syntax, cursor, or request header | `400 Bad Request` |
| External-processing policy or confirmation denial | `403 Forbidden` |
| Resource not found | `404 Not Found` |
| State, uniqueness, or idempotency conflict | `409 Conflict` |
| Stale `If-Match` | `412 Precondition Failed` |
| Body exceeds the endpoint limit | `413 Content Too Large` |
| Unsupported request media type | `415 Unsupported Media Type` |
| Structurally valid JSON with invalid fields | `422 Unprocessable Content` |
| Required `If-Match` omitted | `428 Precondition Required` |
| Request or selected provider rate limited | `429 Too Many Requests` |
| Enabled provider returned an invalid/upstream failure | `502 Bad Gateway` |
| Required local dependency or selected provider unavailable | `503 Service Unavailable` |
| Selected provider timed out | `504 Gateway Timeout` |

`401` and authorization-specific `403` behavior are reserved for a future authenticated deployment. A `Retry-After` header in seconds accompanies retryable `429` and `503` responses when the server can estimate a delay.

### 1.3 Request correlation

- Every request has a canonical lowercase UUIDv4 request ID.
- A client MAY supply it in `X-Request-ID`. If omitted, the API creates one.
- A supplied value that is not a canonical UUIDv4 is rejected with `invalid_request_id`; the error itself receives a new server-generated request ID.
- Every response, including errors and the initial SSE response, returns `X-Request-ID`.
- Successful JSON includes the value in `meta.request_id`; a problem includes it in `request_id`; every SSE data event includes it in `request_id`.
- A request ID is for log correlation only. It is not an idempotency key, job ID, Ask history ID, authentication credential, or external-provider response ID.
- Provider adapters MAY send it as safe request metadata only when the provider supports non-content correlation. Logs record provider/model, timings, and this ID, but MUST NOT record prompts, questions, evidence, answers, paths, secrets, or raw provider events.

## 2. Representation rules

### 2.1 JSON names and values

- JSON property names use `lower_snake_case`.
- Enum values and stable machine codes use `lower_snake_case` ASCII.
- Resource URLs use plural kebab-case nouns, for example `/sync-plans`.
- Acronyms remain lowercase within names: `source_location_id`, `sha256`, `api_version`.
- Boolean names SHOULD be positive (`enabled`, `retryable`) and never encode three states. Use an enum or nullable value when three states are required.
- Byte counts end in `_bytes`; durations end in `_ms`; absolute timestamps end in `_at`.
- SHA-256 values are 64-character lowercase hexadecimal strings.
- Paths are returned only in explicit path fields such as `display_path`. No endpoint accepts a free-form filesystem path for reading a document.
- JSON integers MUST be within JavaScript's safe integer range. Larger future counters must use documented decimal strings rather than silently losing precision.
- Request objects reject unknown properties with `422 validation_failed`; this catches spelling mistakes and prevents accidental policy bypass.
- Response consumers MUST ignore unknown properties for forward compatibility.

For a partial update, omission means “leave unchanged.” Explicit `null` clears a field only if that field is documented as nullable; otherwise it fails validation. Arrays in a merge-patch replace the complete array.

### 2.2 Identifiers

All persisted resource IDs are serialized as canonical lowercase UUID strings. Clients MUST treat them as opaque and MUST NOT infer resource type, creation time, ordering, or content from them.

- Server-assigned mutable/public resource IDs use UUIDv4. Examples include locations, catalog entries exposed as documents, file versions, jobs, duplicate groups, and sync plans.
- Reproducible derived identities use UUIDv5. Examples include deterministic chunk IDs and Qdrant point IDs.
- A random ID MUST never be changed to deterministic generation after data exists without a migration and compatibility review.
- Database sequence numbers MAY exist internally but MUST NOT be exposed as resource identifiers.

The project UUID namespace is fixed as follows:

```text
root namespace = uuid5(UUID_NAMESPACE_URL,
                       "https://github.com/fridlyos/doc_manager")
               = 54a7e032-3fd5-5dbb-a28e-2845b48775c0

chunk namespace         = uuid5(root namespace, "chunk:v1")
                        = d52072c8-003e-53cb-afc4-c15e95663524
qdrant-point namespace  = uuid5(root namespace, "qdrant-point:v1")
                        = 0f1426b1-38a2-5a13-831d-7c6107ef1cc3
content-artifact namespace = uuid5(root namespace, "content-artifact:v1")
                           = 47e686d1-a60a-5205-808b-dc6021dfddd4
```

UUIDv5 names are UTF-8 strings with lowercase hexadecimal hashes and decimal indexes:

```text
chunk name = v1|<content-identity>|<chunking-profile-hash>|<chunk-index>|<chunk-text-sha256>
point name = v1|<chunk-uuid>|<embedding-profile-hash>
artifact name = v1|<extraction-profile-hash>|<normalization-version>|<structure-hash>
```

Fields in these names cannot contain `|`. Any change in name layout requires a new namespace suffix such as `chunk:v2`; it MUST NOT reinterpret existing UUIDs.

Request IDs, idempotency keys, opaque cursors, and request-scoped citation labels are protocol tokens rather than persisted resource IDs. Citation labels such as `E1` deliberately reveal no database identity.

### 2.3 Timestamps

- All API timestamps use RFC 3339 UTC with a literal `Z`, for example `2026-07-11T18:42:07.381Z`.
- Seconds are always present. Fractional seconds MAY be omitted or contain up to six digits; producers trim insignificant trailing zeros.
- Responses MUST NOT emit local offsets, timezone abbreviations, naive timestamps, Unix epoch numbers, or PostgreSQL timestamp text.
- Requests containing an offset MAY be accepted and normalized to UTC, but response values are always canonical UTC.
- A nullable timestamp is `null`, not an empty string or zero date.
- Durations are integer milliseconds and are not timestamps.

## 3. Successful JSON envelopes

Every successful `/api/v1` JSON response uses one of these envelopes. Problems and SSE streams are not wrapped in them.

Single resource or action result:

```json
{
  "data": {
    "id": "3f60f847-5d42-4a8f-9d61-6c0dd12e5e8f",
    "name": "Contracts"
  },
  "meta": {
    "api_version": "1",
    "request_id": "94f14f5a-9b2b-4420-9673-25ed4a10c10e"
  }
}
```

Collection:

```json
{
  "data": [],
  "page": {
    "limit": 50,
    "has_more": false,
    "next_cursor": null
  },
  "meta": {
    "api_version": "1",
    "request_id": "94f14f5a-9b2b-4420-9673-25ed4a10c10e",
    "effective_sort": ["-updated_at", "id"]
  }
}
```

Rules:

- `data` is an object, array, or documented scalar result; it is never omitted on a JSON success.
- An empty collection is `[]`, not `null`.
- `meta.api_version` and `meta.request_id` are always present.
- Endpoint-specific metadata MAY be added under `meta`; domain data does not go there.
- `204` and `304` responses have no body and therefore no envelope.
- A `201` includes the canonical resource URI in `Location`.

## 4. Error contract

### 4.1 Problem Details envelope

Errors follow Problem Details and are never placed inside the success envelope:

```json
{
  "type": "urn:doc-manager:problem:validation_failed",
  "title": "Request validation failed",
  "status": 422,
  "detail": "One or more request fields are invalid.",
  "instance": "urn:doc-manager:request:94f14f5a-9b2b-4420-9673-25ed4a10c10e",
  "code": "validation_failed",
  "request_id": "94f14f5a-9b2b-4420-9673-25ed4a10c10e",
  "retryable": false,
  "errors": [
    {
      "pointer": "/question",
      "code": "required",
      "message": "This field is required."
    }
  ]
}
```

- `type` is always `urn:doc-manager:problem:<code>`.
- `code` is the stable value clients branch on. Its meaning cannot change within API v1.
- `title` is a short stable category label. `detail` and nested `message` are safe human text and MAY be improved without a version change.
- `status` matches the HTTP status before an SSE stream starts. In an in-stream error it records the status that would have been returned.
- `instance` uses the request URN rather than the full URL, so a sensitive query string is never echoed.
- `retryable` tells the client whether retrying later without changing the semantic request can be useful. It is advisory, not permission for unbounded retries.
- `errors` appears only for field-level failures. `pointer` is an RFC 6901 JSON Pointer into the request; header errors use a pointer such as `/headers/idempotency-key`.
- Safe extensions such as `retry_after_seconds`, `current_etag`, `job_id`, or a data-boundary summary MAY appear at the top level and must be documented for that code.
- Problems MUST NOT expose stack traces, SQL, source scan roots, credentials, provider keys/raw bodies, questions, evidence text, document content, or host paths not already authorized for display.

### 4.2 Stable v1 problem codes

| Code | HTTP | Retryable | Meaning |
| --- | ---: | --- | --- |
| `bad_request` | 400 | no | Malformed request not covered by a more precise code. |
| `invalid_request_id` | 400 | no | Supplied `X-Request-ID` is not canonical UUIDv4. |
| `invalid_cursor` | 400 | no | Cursor is malformed, tampered with, or for another query. |
| `cursor_expired` | 400 | yes | Cursor exceeded its supported lifetime. Restart pagination. |
| `idempotency_key_required` | 400 | no | A job-creating POST omitted `Idempotency-Key`. |
| `validation_failed` | 422 | no | JSON or query fields failed typed validation. |
| `not_found` | 404 | no | Requested resource does not exist or is not visible. |
| `conflict` | 409 | no | Current resource state prevents the operation. |
| `idempotency_conflict` | 409 | no | Key was reused with a different semantic request. |
| `idempotency_in_progress` | 409 | yes | The same key is being reserved and no response is available yet. |
| `job_not_cancellable` | 409 | no | Job is already terminal or past its safe cancellation point. |
| `job_not_retryable` | 409 | no | Job state or failure classification does not permit retry. |
| `precondition_required` | 428 | no | A protected mutation omitted `If-Match`. |
| `precondition_failed` | 412 | no | The supplied ETag is stale. Refetch before editing. |
| `source_unavailable` | 503 | yes | Source mount/sentinel cannot be safely reached; no missing-file reconciliation occurred. |
| `dependency_unavailable` | 503 | yes | Required local service is not ready. |
| `insufficient_evidence` | — | no | **Not a problem.** It is a successful Ask result state. |
| `external_confirmation_required` | 403 | no | External provider selected without request acknowledgment; no provider call occurred. |
| `external_policy_denied` | 403 | no | Deployment or any evidence-bearing source denies external generation; no provider call occurred. |
| `provider_unavailable` | 503 | yes | Explicitly selected provider/model is not ready; no fallback occurs. |
| `provider_authentication_failed` | 502 | no | Server-side provider credential/configuration was rejected. |
| `provider_rate_limited` | 429 | yes | Selected provider rejected the request for rate limits. |
| `provider_timeout` | 504 | yes | Selected provider exceeded its bounded timeout. |
| `provider_error` | 502 | depends | Selected provider returned an invalid or failed response. |

Endpoint-specific extraction and indexing errors use the same lower-snake-case convention when represented through HTTP. Stored job/file errors include a stable `code`, safe `message`, and `retryable` value, but are not themselves HTTP Problem Details until an API operation fails.

## 5. Collection pagination, sorting, and filtering

### 5.1 Cursor pagination

All potentially large GET collections use keyset cursor pagination:

```text
GET /api/v1/documents?limit=50&sort=-updated_at&filter[state]=indexed
GET /api/v1/documents?limit=50&sort=-updated_at&filter[state]=indexed&cursor=<opaque-token>
```

- `limit` defaults to `50`, has a minimum of `1`, and a maximum of `100` unless an endpoint documents a smaller maximum.
- `cursor` is an opaque, URL-safe token. Clients store and return it unchanged and MUST NOT decode or construct it.
- The cursor binds to the route, normalized filters, effective sort, and last key values. Using it with different filters or sorting returns `invalid_cursor`.
- Cursors SHOULD be integrity protected and short lived. The first implementation supports them for at least 15 minutes; expiration returns `cursor_expired`.
- `next_cursor` is `null` when `has_more` is false.
- A total count is omitted by default because it can be expensive and unstable. Endpoints MAY expose an explicit count option later.
- Pagination is not a database snapshot. Concurrent inserts/updates can move records; keyset ordering prevents ordinary offset drift but does not promise a frozen result set.

### 5.2 Sorting

- `sort` is a comma-separated list of allowlisted field names.
- Prefix descending fields with `-`; ascending has no prefix. Example: `sort=-updated_at,name`.
- Unknown, duplicated, or non-sortable fields return `validation_failed`.
- The server appends `id` in ascending order as a unique tie-breaker when absent and reports the final list in `meta.effective_sort`.
- Null values sort last in both directions unless an endpoint explicitly documents otherwise.
- Each collection documents its default and allowed sort fields in OpenAPI.

### 5.3 Filtering

- Filters use repeated `filter[field]` query parameters. Example: `filter[state]=indexed&filter[state]=failed`.
- Multiple values for one field are ORed; different fields are ANDed.
- Empty values, empty arrays, unknown fields, and unsupported operators fail with `validation_failed`; omission means no constraint.
- Enum matching is exact and case-sensitive. Extension values are normalized lowercase without a leading dot.
- Timestamps use explicit endpoint fields such as `filter[updated_at_gte]` and the timestamp rules above.
- Text query behavior is endpoint-specific and does not imply filesystem-path search.
- Clients MUST percent-encode query parameters normally. The bracket notation shown above is the decoded form.

POST `/search` and `/ask` use the common typed filter object instead of query parameters:

```json
{
  "source_location_ids": ["3f60f847-5d42-4a8f-9d61-6c0dd12e5e8f"],
  "document_ids": [],
  "extensions": ["pdf", "md"],
  "tags": ["contract"],
  "tag_mode": "all"
}
```

Arrays MUST be omitted rather than sent empty. The example includes `document_ids: []` only to show that such input is invalid. Within an array values are ORed, except tags use `tag_mode: "all" | "any"`. Different filter fields are ANDed. No filter accepts `scan_root`, `display_path`, UNC path, drive letter, or arbitrary path text.

## 6. Idempotency and durable jobs

### 6.1 Idempotency keys

These job-creating POSTs require an `Idempotency-Key` header:

- `/api/v1/locations/{id}/scan`
- `/api/v1/documents/{id}/reindex`
- `/api/v1/jobs/{id}/retry`
- `/api/v1/sync-plans`
- any later POST documented as creating durable background work

Rules:

- A key is 16–128 visible ASCII characters; UUIDv4 is recommended. It is case-sensitive and is never logged as content.
- The key is scoped to deployment/user identity, HTTP method, and canonical route template. Route parameters and the semantic request fingerprint are still compared.
- The fingerprint covers route parameters, normalized query/body, provider selection, and relevant conditional headers. It excludes `X-Request-ID` and transport-only headers.
- The key reservation and durable job creation occur atomically in PostgreSQL.
- Repeating the same semantic request with the same key returns the original job and original status. The current request gets a new request ID, `Idempotency-Replayed: true`, and `meta.idempotency_replayed: true`.
- Reusing a key for a different semantic request returns `idempotency_conflict` and creates nothing.
- A rare concurrent reservation with no committed response returns retryable `idempotency_in_progress` rather than creating a second job.
- Records are retained for at least 24 hours and at least until the referenced job is terminal, whichever is longer. Clients must not intentionally reuse a key after expiry.
- Domain coalescing is separate from request idempotency. For example, a new scan request may return an already queued/running location scan with `meta.coalesced: true` even when it has a new idempotency key.

The idempotency lookup happens before reevaluating a now-stale precondition on an exact replay. This permits safe recovery when the first successful response was lost.

### 6.2 Accepted-job response

Job-creating endpoints return `202`, `Location: /api/v1/jobs/{job_id}`, and a short `Retry-After` polling hint:

```json
{
  "data": {
    "id": "df93fe2a-af79-4e19-b505-7bb64f42a18a",
    "job_type": "scan_location",
    "status": "queued",
    "priority": 0,
    "target": {
      "resource_type": "source_location",
      "resource_id": "3f60f847-5d42-4a8f-9d61-6c0dd12e5e8f"
    },
    "progress": {
      "current": 0,
      "total": null,
      "unit": "files"
    },
    "attempt_count": 0,
    "max_attempts": 3,
    "requested_at": "2026-07-11T18:42:07.381Z",
    "started_at": null,
    "finished_at": null,
    "cancel_requested_at": null,
    "error": null
  },
  "meta": {
    "api_version": "1",
    "request_id": "94f14f5a-9b2b-4420-9673-25ed4a10c10e",
    "idempotency_replayed": false,
    "coalesced": false
  }
}
```

Initial `job_type` values are `scan_location`, `index_file`, `remove_stale_vectors`, `reindex_document`, `reindex_all_for_profile`, `build_duplicate_report`, `build_sync_plan`, and `catalog_consistency_check`.

Job status values and transitions are:

```text
queued      -> running | cancelled
running     -> succeeded | failed | retry_wait | cancelled
retry_wait  -> queued | cancelled
succeeded   -> terminal
failed      -> terminal; POST /retry creates a new linked job
cancelled   -> terminal; POST /retry may create a new linked job when allowed
```

- A cancellation request records `cancel_requested_at`; it does not invent a `cancel_requested` status. The job remains in its current state until the worker reaches a safe boundary.
- Repeating cancellation while cancellation is pending returns the current job. Cancelling a terminal/non-cancellable job returns `job_not_cancellable`.
- `/jobs/{id}/retry` creates a new UUIDv4 job with `retry_of_job_id`; it never rewrites terminal history.
- `progress.total` is nullable until known. A percentage is derived only when total is positive; the API does not report fake percentages.
- A job `error` is either `null` or `{ "code": string, "message": string, "retryable": boolean }`. It contains no traceback or document body.
- Worker leases, owners, and heartbeat internals are not public API fields. User-relevant attempt/progress timestamps are public.

## 7. Conditional requests and concurrency

Mutable configuration resources, beginning with source locations, use optimistic concurrency:

- `GET` returns a strong `ETag`, for example `"location-3f60f847-5d42-4a8f-9d61-6c0dd12e5e8f-7"`, and a matching integer `revision` in the representation.
- Any operational or configuration update that changes the representation increments its revision.
- `PATCH /api/v1/locations/{id}` uses `application/merge-patch+json` and requires `If-Match` with the exact current ETag.
- State-changing location actions such as `/locations/{id}/disable` also require `If-Match`.
- Missing `If-Match` returns `precondition_required`. A stale value returns `precondition_failed` with the current ETag in both the `ETag` header and safe `current_etag` problem extension.
- A successful mutation returns the updated representation and new ETag.
- `If-None-Match` on GET MAY return `304`.
- `If-Match: *` is not accepted for protected mutations in v1.

Job claims, retry/cancel transitions, scans, and sync-plan generation use atomic domain-state checks rather than client ETags. Generated sync plans are immutable in the MVP. Collection pagination does not imply a lock or snapshot.

## 8. Provider-neutral Ask contract

### 8.1 Common request

`POST /api/v1/ask` and `POST /api/v1/ask/stream` accept the same JSON body. Ask is stateless; there is no conversation or provider response ID in v1.

```json
{
  "question": "When does the Acme agreement renew?",
  "provider_id": "ollama",
  "external_processing_acknowledged": false,
  "filters": {
    "source_location_ids": ["3f60f847-5d42-4a8f-9d61-6c0dd12e5e8f"],
    "extensions": ["pdf"]
  },
  "retrieval": {
    "top_k": 12,
    "score_threshold": 0.42
  },
  "generation": {
    "max_output_tokens": 1200
  }
}
```

- `question` is required, non-blank UTF-8 text with an implementation limit documented in OpenAPI.
- `provider_id` is required and must name an enabled adapter. There is no automatic local/external or provider-to-provider fallback.
- An optional `model_id` may select only a model exposed by that enabled provider configuration. If omitted, the provider's configured active model is used. Arbitrary endpoints or base URLs are never accepted.
- `external_processing_acknowledged` MUST be `true` when the selected provider's data boundary is `external`. It does not override deployment allowlists or per-source `deny` policy.
- External policy is checked after retrieval against every evidence-bearing source and before the provider request. A denial fails closed; evidence is not silently removed and another provider is not selected.
- `retrieval` and `generation` fields are bounded by server policy. Omission selects the active profile; clients cannot override embedding profiles, system grounding instructions, external endpoint, tools, storage behavior, or secret configuration.
- OpenAI requests remain stateless with `store=false`, no hosted files/file search, web search, tools, file uploads, background mode, or conversation chaining.
- An empty or weak evidence set returns `status: "insufficient_evidence"` without invoking any generation provider.

If external acknowledgment is absent, `external_confirmation_required` MAY include this safe extension after local retrieval:

```json
{
  "data_boundary": {
    "classification": "external",
    "provider_id": "openai",
    "evidence_blocks": 4,
    "evidence_characters": 9210,
    "paths_sent": 0,
    "file_names_sent": 0,
    "tags_sent": 0,
    "catalog_ids_sent": 0
  }
}
```

It contains counts only, never evidence text. No provider call has occurred in this state.

### 8.2 Normal Ask response

`POST /api/v1/ask` returns the standard single-result envelope. Its `data` object is a discriminated result:

```json
{
  "data": {
    "id": "de16393c-a2da-46f4-a5dd-5cfeab3be5ac",
    "status": "completed",
    "answer": "The agreement renews on 31 December 2026 [1].",
    "answer_format": "markdown",
    "provider": {
      "provider_id": "ollama",
      "model_id": "llama3.1:8b",
      "data_boundary": "local",
      "invoked": true
    },
    "data_boundary": {
      "classification": "local",
      "external_processing_acknowledged": false,
      "external_request_attempted": false,
      "external_transfer_occurred": false,
      "external_payload": {
        "question_sent": false,
        "grounding_instructions_sent": false,
        "evidence_blocks_sent": 0,
        "evidence_characters_sent": 0,
        "opaque_citation_ids_sent": 0,
        "paths_sent": 0,
        "file_names_sent": 0,
        "tags_sent": 0,
        "catalog_ids_sent": 0,
        "original_files_sent": 0
      }
    },
    "retrieval": {
      "candidate_count": 8,
      "selected_evidence_count": 3,
      "sufficient": true
    },
    "citations": [
      {
        "citation_id": "E1",
        "ordinal": 1,
        "chunk_id": "a13b9d48-6a5c-5a6b-9d1a-e0dbe4fa6e90",
        "page_start": 4,
        "page_end": 4,
        "section": null,
        "snippet": "The term renews through 31 December 2026 ...",
        "similarity_score": 0.8124,
        "availability": "current",
        "paths": [
          {
            "catalog_entry_id": "2bb50435-098d-4d95-a869-aad1ac6d8c16",
            "source_location_id": "3f60f847-5d42-4a8f-9d61-6c0dd12e5e8f",
            "display_path": "\\\\nas\\legal\\contracts\\acme.pdf",
            "state": "indexed",
            "is_primary": true
          }
        ]
      }
    ],
    "finish_reason": "stop",
    "usage": {
      "input_tokens": 1842,
      "output_tokens": 22,
      "total_tokens": 1864
    },
    "timing": {
      "retrieval_ms": 94,
      "generation_ms": 683,
      "total_ms": 787
    },
    "warnings": []
  },
  "meta": {
    "api_version": "1",
    "request_id": "94f14f5a-9b2b-4420-9673-25ed4a10c10e"
  }
}
```

Result rules:

- `id` is a request-scoped UUIDv4 Ask ID. With history disabled it is not retained as a conversation.
- `status` is `completed`, `insufficient_evidence`, or `refused`.
- `answer` is non-empty only for `completed`; it is `null` otherwise. Markdown is untrusted output and the UI must sanitize it.
- For `insufficient_evidence`, `provider.invoked` is false, citations are empty, `finish_reason` is `insufficient_evidence`, generation timing is `null`, usage is `null`, and no external transfer occurs.
- A valid model refusal is a `200` result with `status: "refused"`, not an infrastructure problem. `answer` is null and `finish_reason` is `refusal`.
- Provider/model identify the adapter and model actually selected. Raw Ollama/OpenAI response types and response IDs never appear.
- Usage is `null` when unavailable; token counts are provider-reported and are not assumed comparable across providers.
- `similarity_score` is meaningful only within the active embedding profile; higher is better, but clients must not compare values across profiles.
- `availability` is `current`, `missing`, or `historical`. Every citation has at least one locally resolved path or is explicitly marked missing/historical.
- Paths, pages, snippets, and catalog IDs are resolved by the server from PostgreSQL evidence. Provider-generated paths are ignored.
- The provider receives request-scoped aliases such as `E1`; it never receives `chunk_id`, catalog IDs, paths, filenames, tags, or source names. The server maps valid aliases to citations and converts answer markers to `[1]`. Unknown aliases are removed as citations and add `unknown_provider_citation_removed` to `warnings`.
- For an external provider, `classification` is `external`. Once the HTTP client begins writing the provider request, both `external_request_attempted` and the conservatively named `external_transfer_occurred` are true even if the upstream response later fails. The payload summary reports the fields/counts actually attempted and all metadata/file counters remain zero.

### 8.3 Streaming Ask over SSE

`POST /api/v1/ask/stream` is consumed with streaming `fetch`, not browser `EventSource` (which cannot send the required POST JSON body).

Response headers:

```text
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
X-Content-Type-Options: nosniff
X-Request-ID: <uuid-v4>
Docman-Api-Version: 1
```

Proxies MUST NOT buffer or transform the stream. The server SHOULD emit an SSE comment `: keep-alive` at least every 15 seconds while idle; comments are not events and have no sequence number.

Each event has an incrementing decimal SSE `id`, a provider-neutral event name, and one single-line JSON `data` object:

```text
id: 1
event: ask.started
data: {"stream_version":"1.0","sequence":1,"request_id":"94f14f5a-9b2b-4420-9673-25ed4a10c10e","ask_id":"de16393c-a2da-46f4-a5dd-5cfeab3be5ac","occurred_at":"2026-07-11T18:42:07.381Z","provider":{"provider_id":"ollama","model_id":"llama3.1:8b","data_boundary":"local"}}

```

All data events contain `stream_version`, `sequence`, `request_id`, `ask_id`, and `occurred_at`. Provider SDK event names and payloads MUST be normalized and MUST NOT pass through to clients.

| Event | Cardinality | Payload beyond common fields |
| --- | --- | --- |
| `ask.started` | exactly once, first | Selected `provider` and initial `data_boundary`. |
| `retrieval.completed` | exactly once | `retrieval` summary with candidate/selected counts and sufficiency. No evidence text. |
| `generation.started` | zero or once | Actual provider/model and updated boundary/payload counts. Absent for insufficient evidence. |
| `answer.delta` | zero or more | `delta`, a non-empty text fragment. Fragments concatenate in sequence order. |
| `citation.resolved` | zero or more | One complete server-resolved `citation`; each citation ID is emitted at most once. |
| `ask.warning` | zero or more | Stable warning `code` and safe `message`. |
| `ask.result` | exactly once on semantic success | `data`, exactly the normal Ask result object including the full authoritative answer and citations. Terminal. |
| `ask.error` | exactly once on failure after streaming began | `problem`, the complete Problem Details object. Terminal. |

Ordering and termination rules:

1. The server performs normal request validation, provider readiness checks, external-policy checks, and any failure that can be known safely before committing SSE headers. Such failures return an ordinary `application/problem+json` response with its real HTTP status.
2. `ask.started` is first, followed by `retrieval.completed`.
3. Insufficient evidence goes directly to `ask.result`; no `generation.started` or deltas occur.
4. Otherwise `generation.started` precedes all `answer.delta` events. Citation and warning events may interleave with deltas.
5. Exactly one terminal event is sent: `ask.result` or `ask.error`. No `[DONE]` sentinel is used.
6. `ask.result.data` is authoritative. A client may show accumulated deltas immediately, then replace/reconcile them with the terminal full result.
7. After `ask.error`, accumulated deltas are incomplete and MUST NOT be presented as a completed answer.
8. If the connection drops, the API cancels retrieval/provider work on a best-effort basis and never switches provider. Ask streams are not persisted or replayable in v1; `Last-Event-ID` does not resume a stream.

An in-stream failure example is:

```text
id: 7
event: ask.error
data: {"stream_version":"1.0","sequence":7,"request_id":"94f14f5a-9b2b-4420-9673-25ed4a10c10e","ask_id":"de16393c-a2da-46f4-a5dd-5cfeab3be5ac","occurred_at":"2026-07-11T18:42:17.381Z","problem":{"type":"urn:doc-manager:problem:provider_timeout","title":"Generation provider timed out","status":504,"detail":"The selected provider did not finish within the configured timeout.","instance":"urn:doc-manager:request:94f14f5a-9b2b-4420-9673-25ed4a10c10e","code":"provider_timeout","request_id":"94f14f5a-9b2b-4420-9673-25ed4a10c10e","retryable":true}}

```

## 9. Compatibility and versioning

### 9.1 API v1 guarantees

Within `/api/v1`, the implementation MAY make these additive changes:

- add optional response properties;
- add endpoints or optional request properties;
- add non-terminal SSE event types;
- add documented filters/sort fields;
- add problem codes for new failure categories;
- add provider adapters while retaining the provider-neutral shapes.

Clients MUST ignore unknown response properties and unknown non-terminal SSE events. Clients SHOULD render an unknown enum value as “unknown” rather than crash, but the server will avoid adding values to closed state-machine enums without documenting the behavior.

These changes require `/api/v2` or a separately negotiated new stream version:

- remove or rename a field/event;
- change a field's type, nullability, units, or meaning;
- change identifier or timestamp formats;
- change an existing enum/code's meaning;
- relax an external-processing guard in a way that could create new egress;
- reorder required SSE lifecycle events or remove a terminal event;
- reinterpret existing deterministic UUID namespaces.

Unknown request fields remain rejected. Adding an optional request field is compatible because older clients do not send it; clients must upgrade before using it.

### 9.2 Deprecation and generated contract

- Deprecated endpoints/fields remain functional for a documented window and are marked in OpenAPI.
- Endpoint deprecation uses standard `Deprecation`, `Sunset`, and `Link` headers when a replacement exists.
- The implementation's generated OpenAPI document is the executable schema, but it MUST conform to this contract. Contract tests snapshot critical schemas, problem examples, pagination, idempotent replays, ETags, and the complete SSE event sequence.
- Operation IDs in OpenAPI are stable lower-snake-case names and are not derived from Python function names.
- Cursors, ETags, request IDs, and provider SDK identifiers are not compatibility surfaces beyond the behavior explicitly stated here.

## 10. Security invariants visible in the API

- Search remains available even when no generation provider is ready.
- Every Ask explicitly names one enabled provider; failure never triggers another provider.
- External generation requires deployment opt-in, allowlisted adapter/model, source-level allow for every evidence block, and request acknowledgment.
- External payloads contain only grounding instructions, the current question, selected evidence text, and request-scoped citation aliases. Paths, filenames, tags, catalog/source/document/chunk IDs, source names, original files, and prior history remain local.
- Citations and display paths returned to the browser are resolved locally after retrieval/generation and are never trusted from model output.
- Provider status endpoints expose credential presence/readiness, never credential values or raw upstream diagnostics.
- Errors, logs, response metadata, cursors, and ETags never embed document text, questions, answer text, secrets, or filesystem scan roots.
- No API route reads a caller-supplied path. Resource IDs and allowlisted source-location configuration are the only route to document metadata.
