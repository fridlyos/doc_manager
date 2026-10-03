# Known limitations & deferred features

What the MVP deliberately does **not** do, so operators are not surprised. These
are scope boundaries, not bugs; each notes the workaround or the authority that
deferred it.

## Processing / indexing

- **No OCR.** Only the embedded text layer of PDFs and plain TXT/MD is extracted.
  Scanned/image-only PDFs index with little or no text and will not be found by
  search. OCR is a post-MVP decision (README §Open Decisions).
- **First supported types only: PDF, TXT, MD.** Other extensions are cataloged but
  marked `unsupported` (visible in the error queue) and are not indexed.
- **Single active embedding profile.** One embedding model/vector size is active at
  a time. Changing `DOCMAN_EMBEDDING_MODEL` is a full re-index (new profile hash);
  old vectors are retired via `POST /system/remove-stale-vectors`. The system does
  not serve multiple profiles concurrently. See
  [`upgrade-migration.md`](upgrade-migration.md).
- **External embeddings not implemented.** Embeddings are always local (FastEmbed).
  Only *generation* can be external (OpenAI, opt-in) — never embeddings, which
  would mean sending the whole corpus rather than selected evidence
  ([`external-processing.md`](external-processing.md)).

## Synchronization

- **Reporting only — no execution.** Multi-location sync produces a **dry-run
  plan** (copy/conflict/coverage); it never copies, moves, or deletes a file.
  There is no apply/execute route and no execution columns (ADR 0006). Acting on a
  plan is a manual operator step outside the app.

## Generation / Ask

- **No conversation history or retention.** Ask is stateless: each question is
  answered from freshly retrieved evidence. Questions, answers, and citations are
  not persisted as a history, and the OpenAI adapter sends `store=false`. There is
  no "previous answers" view to revisit.
- **No automatic local↔external fallback.** The provider is chosen explicitly; a
  failed or denied external request does not silently fall back to local (or vice
  versa). Search and local Ask keep working when external is disabled.

## Storage / recovery

- **Restore requires the live sources.** The vector index is **not** stored in a
  backup set; it is rebuilt from the catalog + the live source documents
  (`reindex ?rebuild_vectors=true`). Restore therefore needs the source mount
  present and unchanged. PostgreSQL is the recovery authority
  ([`backup-restore.md`](backup-restore.md)).
- **PITR off by default.** Recovery point is up to ~24h (the nightly logical dump).
  WAL-archiving PITR is documented but not wired ([`postgresql-pitr.md`](postgresql-pitr.md)).
- **Mapped-drive caveats (Windows).** Drive-letter mappings are per Windows logon
  and must exist in the same logon that runs Docker Desktop; a mapping made under
  another account is invisible to Compose. Live PostgreSQL/Qdrant volumes must
  never live on the mapped NAS/SMB drive — local SSD only ([`runbook.md`](runbook.md)).
- **Migrations are manual.** `alembic upgrade head` is run explicitly, not at
  container start ([`upgrade-migration.md`](upgrade-migration.md)).

## Deployment / security

- **Single-operator, local deployment.** No authentication, no TLS, no
  multi-tenant isolation. Only the API port is published, bound to `127.0.0.1`.
  Adding auth/TLS is required before exposing the service on a LAN (out of MVP
  scope; `runbook.md`).
- **The allowlist + read-only mounts are the trust anchors.** A permissive
  `DOCMAN_ALLOWED_SOURCE_ROOTS` or a read-write source mount weakens the filesystem
  boundary (`docs/security/threat-model.md`).

## Related

- [`backup-restore.md`](backup-restore.md) · [`upgrade-migration.md`](upgrade-migration.md)
  · [`provider-operations.md`](provider-operations.md) · [`runbook.md`](runbook.md)
  · `docs/security/threat-model.md`.
