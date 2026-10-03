# Phase 8 Progress Status

**Branch:** `phase-8-hardening-release` (cut from `main` @ `3349cee`, Phase 7 merged via PR #8).

Scope = TECHSTACK §14 "Phase 8: Hardening and MVP Release".

Phase 8 is the **release** phase: harden the runtime, complete and test the
application-aware backup/restore + NAS workflow, review and test the security
boundary (filesystem access, prompt injection, secret/egress/metadata disclosure),
measure performance, review accessibility, write the operator guides, and prove the
whole `README.md` Definition-of-Done end to end. Mostly **verify / complete / test /
document** over the features built in Phases 1–7 — little new product surface.

## Deliverables

| # | Deliverable | Status |
| --- | --- | --- |
| 8.a | Resource limits, graceful shutdown, stale-lease recovery, cleanup grace periods | **✅ complete** |
| 8.b | Threat-model review — filesystem access + prompt injection | **✅ complete** |
| 8.c | Coordinated PG dump + Qdrant snapshot + artifact inventory + checksums + atomic completion + retention (backup maintenance profile) | **✅ complete** |
| 8.d | Backup/restore, optional PostgreSQL PITR, upgrade/migration, model-setup, troubleshooting guides | **✅ complete** |
| 8.e | Provider enablement, key rotation, external-data review, rate-limit/cost-control, incident-disable procedures | ⬜ not started |
| 8.f | Threat-model tests — secret leakage, accidental egress, prompt injection, path/metadata disclosure | **✅ complete** |
| 8.g | Connect completed-backup directory to NAS external-backup workflow without exposing live volume internals | ⬜ not started |
| 8.h | Performance measurements on a representative local corpus | ⬜ not started |
| 8.i | Accessibility + browser workflow review | ⬜ not started |

## Exit criteria (whole phase)

1. All MVP Definition-of-Done items in `README.md` pass end to end (add location →
   scan/index → ask → cited local answer → re-index → status/failures).
2. Backup/restore, NAS external-copy detection, and fresh-install procedures are tested.
3. A full restore into empty volumes passes catalog/artifact/vector **consistency**
   and **known-query** checks.
4. Known limits and deferred features are documented.

## Already in place (reused / to verify, not rebuilt)

- **Graceful shutdown:** `JobEngine.release_for_shutdown` + `ShutdownRequestedError`
  + `ctx.check_boundary()` release running jobs to `retry_wait` at the nearest safe
  checkpoint (Phase 2). 8.a **verifies + tests** it and adds resource limits.
- **Stale-lease recovery:** the reaper (`queue.py`) reclaims expired leases
  (`lease_expired` → retry/fail). 8.a **verifies + tests** it.
- **Backup scaffolding:** `scripts/backup.sh`, `restore.sh`, `verify-backup.sh`,
  `bootstrap.sh`, `check.sh` + the `backup` maintenance compose profile
  (`--profile maintenance`, NAS mounted **only** into that service) + §11 storage
  layout. 8.c/8.g **complete + test** the coordinated set, checksums, atomic
  completion, retention, and the NAS `.partial → completed` marker flow.
- **Ops docs:** `docs/operations/{backup-restore, external-processing,
  provider-configuration, runbook}.md`. 8.d/8.e **extend** (PITR, upgrade/migration,
  model setup, troubleshooting, key rotation, cost control, incident-disable).
- **Consistency + rebuild:** `catalog_consistency_check` (Phase 4.e),
  `remove_stale_vectors`, `reindex_all_for_profile` (Phase 6) — the restore
  consistency check + vector rebuild-from-catalog path (§11.4).
- **Security boundary:** external-processing policy + zero-metadata data-boundary
  counters (Phase 5.c), server-owned citations (5.e), Docker-secret keys (5.d),
  read-only sources, display-path-only responses. 8.b/8.f **review + test** these.
- **Migrations 0001–0006** + Alembic; upgrade/downgrade round-trips proven per phase.

## Contract / spec anchors (TECHSTACK §11, §12; README DoD)

- **Backup authority (§11.4):** PostgreSQL dump is authoritative; the Qdrant
  snapshot is a fast-recovery convenience; the vector index is **rebuildable** from
  the catalog + artifacts/sources. Restore always runs the consistency checker.
- **Canonical backup set (§11.5):** PG custom-format dump + globals, Qdrant
  collection snapshot (+ alias/profile mapping recorded separately), artifact
  inventory, SHA-256 checksums, machine-readable manifest, verify → NAS
  `incoming/` → verify → atomic rename to `completed/` → completion marker last.
- **Retention/RPO/RTO (§11.6):** PG nightly (14 daily / 8 weekly / 12 monthly);
  Qdrant nightly + pre-upgrade.
- **Security (§12):** secrets only via Docker secrets into the API service; never to
  browser/worker/DB/logs/backups; no arbitrary user-path reads; treat document text
  as untrusted; external egress only after policy gates.

---

## Completed work

### 8.a — Runtime hardening ✅ (2026-08-06)

Delivered: `compose.yaml` `deploy.resources.limits` (cpus+memory, `.env`-tunable)
on postgres/qdrant/api/worker (worker 4c/4g for the embedding model). Verified
**graceful shutdown** (`release_for_shutdown` → `retry_wait`, attempt retained; new
test: shutdown re-queues, a second worker reclaims + runs). Stale-lease reaper
already tested (no change). New `JobEngine.gc_stale_rows` (worker `_maintenance_loop`,
`maintenance_interval_seconds`/`maintenance_retention_hours`) GCs abandoned
`scan_observations` (terminal job, aged) + expired `idempotency_records` (aged, job
terminal/absent); never touches open work. 3 integration tests; full backend suite
**288 pass, 1 skipped**; ruff/mypy clean; compose validates. **Full report:
`docs/architecture/phase-8a-runtime-hardening.md`.**

### 8.b — Threat-model review (filesystem + prompt injection) ✅ (2026-08-06)

Delivered `docs/security/threat-model.md`: assets + trust boundaries, then filesystem
threats (T-FS-1 arbitrary read → allowlist/no path-read endpoints; T-FS-2 symlink
escape → never-follow-symlinks; T-FS-3 source mutation → read-only + derived stores +
no-exec sync; T-FS-4 host-path disclosure → display_path only, scan_root only on the
operator's own location resource; T-FS-5 mapped-drive swap → sentinel identity) and
prompt-injection threats (T-PI-1 instruction hijack → untrusted-evidence framing;
T-PI-2 fabricated citation → server-owned citations + drop invented aliases; T-PI-3
tool execution → no tools exposed; T-PI-4 evidence exfil → external policy +
zero-metadata boundary). Each threat maps control (with code ref) → residual risk →
test; a traceability table links to the Phase 8.f tests. Docs-only.

### 8.c — Coordinated backup maintenance profile ✅ (2026-08-06)

Delivered `backend/src/doc_manager/backup/` — a testable orchestrator. `run_backup`
builds a set in local **staging** (`pg_dump --format=custom`, `pg_dumpall
--globals-only`, optional Qdrant snapshot + collection/profile mapping, checksummed
artifact inventory), writes `manifest.json` + `SHA256SUMS`, **verifies**, then
publishes atomically: `copytree → completed/<id>.partial → verify → rename → write
COMPLETED marker LAST`, then prunes per GFS (`retention.py`: 14 daily / 8 weekly /
12 monthly). External captures are injected (`BackupSteps` Protocol; `DefaultSteps`
for real, fakes in tests) so the discipline is unit-tested without live services —
an interrupted run and a staged-verify failure both leave **no** completed set. No
secrets enter the manifest (non-secret config checksum only); the artifact store is
read **read-only**; no source root is written. New `deploy/backup.Dockerfile`
(worker image + PGDG **postgresql-client-16**); `compose.yaml` `backup` service now
uses it, mounts the artifact volume read-only, and drops the `sleep infinity`
entrypoint so a passed script runs. `scripts/backup.sh` / `verify-backup.sh` are now
thin wrappers over `python -m doc_manager.backup {run,verify}`. 13 unit tests; full
backend suite **301 pass, 1 skipped**; ruff/format/mypy clean. **Full report:
`docs/architecture/phase-8c-backup.md`.**

### 8.d — Backup/restore & lifecycle guides ✅ (2026-10-03)

Extended `docs/operations/`. **Rewrote `backup-restore.md`** — was a stale "Phase 1
skeleton"; now documents the real 8.c coordinator (set contents, staging→verify→
atomic-publish→`COMPLETED`-last→GFS prune), verification, and an honest **manual
empty-volume restore procedure** (fresh volumes → `psql globals.sql` +
`pg_restore` via libpq `PG*` env → Qdrant snapshot restore *or*
`POST /system/reindex` rebuild → known-query validation). States plainly that
`scripts/restore.sh` is still a skeleton and the scripted drill + automated
`catalog_consistency_check` gate land with the release DoD. **New docs:**
`postgresql-pitr.md` (document-only per open decision #5 — nightly logical dump is
the MVP authority; records why WAL-archiving PITR is off by default + an enable
sketch), `upgrade-migration.md` (back-up-first; `alembic upgrade head` is manual,
not auto-run; upgrade sequence with graceful worker stop; **when a re-index is
required** = embedding-profile change only, provider switch never; PG-major
dump/restore + Qdrant rebuild), `model-setup.md` (Ollama chat-model pull; FastEmbed
embedding model downloads on first embed with **no persistent cache volume** →
re-download on container recreate, pre-warm + override guidance), and
`troubleshooting.md` (symptom-grouped: readiness/Qdrant-fs-check/migrations,
mapped-drive + `unavailable` locations, model/generation, backup/restore,
jobs/reaper/GC). All cross-linked; env-var names verified against
`core/config.py` (`DOCMAN_` prefix). Docs-only — no code, tests, or gates touched.

### 8.f — Threat-model tests ✅ (2026-10-03)

New `backend/tests/unit/test_threat_model.py` — **15 offline tests**, each named
for the threat it guards, making the 8.b controls executable:

- **Secret leakage:** `read_openai_api_key` is file-only (unset/missing → `None`,
  present → stripped) and the **only** `openai_api_key*` reader on `Settings`; the
  log redactor scrubs the key value and the content keys; the backup manifest's
  `_config_snapshot` holds only the four non-secret indexing keys — the secret
  never appears.
- **Accidental egress (T-PI-4):** `evaluate_external_policy` fails closed when
  external is disabled (even acknowledged) and when any one source denies; the
  deny reason carries no source name; the boundary's metadata counters
  (`paths/file_names/tags/catalog_ids/original_files_sent`) are **structurally
  zero** on local, on a real external attempt, and on the default payload.
- **Prompt injection (T-PI-1/2):** the system prompt frames evidence as untrusted
  and the injection-laden block is delivered only as `[E1]` data *after* the
  grounding frame (never promoted to a rule); `map_citations` drops an invented
  alias with `unknown_provider_citation_removed`, keeps citations **server-owned**
  (the cited path is the server `ResolvedPath`, never the model's `/etc/shadow`),
  and a path written in prose yields no citation.
- **Path disclosure (T-FS-4):** the citation/search serializers expose
  `display_path` only; `scan_root` is serialized **solely** by
  `serialize_location` and by no evidence-facing serializer.

Endpoint-level controls (allowlist rejection, symlink skipping, sync no-write,
the real OpenAI request contract) remain covered by the integration suite and
`test_locations_browse`/`test_sync_plan`/`test_openai_provider`; the 8.b
traceability table now names the guarding test per threat. Offline suite **221
passed, 96 skipped** (integration needs compose PG); ruff/format/mypy clean (102
source files).

---

## Planned work

### 8.a — Runtime hardening

- **Resource limits:** add `deploy.resources.limits`/reservations (or `mem_limit`/
  `cpus`) to `compose.yaml` for api / worker / postgres / qdrant; document the
  defaults and how to tune. Worker embedding memory bounded by
  `embedding_batch_size` (already configurable).
- **Graceful shutdown (verify + test):** SIGTERM → stop claiming, release running
  jobs to `retry_wait` within `worker_shutdown_grace_seconds`, then exit. Add an
  integration test that a shutdown mid-job re-queues it without loss/duplication.
- **Stale-lease recovery (verify + test):** reaper reclaims expired leases; test a
  crashed worker's job is reclaimed and completes on a second worker.
- **Cleanup grace periods:** garbage-collect abandoned `scan_observations` from
  interrupted scans, expired `idempotency_records` (≥24 h + terminal job), and an
  optional retention for old `sync_plans` / `duplicate_*` snapshots — a durable
  `catalog_maintenance`/GC tick or a documented manual job. *Open decision #2.*

Tests: shutdown re-queue; reaper reclaim; GC removes only safely-abandoned rows.

### 8.b — Threat-model review (filesystem + prompt injection)

`docs/security/threat-model.md`: enumerate assets, trust boundaries, and threats
for (1) **filesystem access** — path traversal, symlink escape, mapped-drive
identity, read-only enforcement, no user-supplied path reads; and (2) **prompt
injection** — document text framed as untrusted evidence, server-owned citations,
no tool/command execution from evidence. Map each threat to the existing control
and to a test (8.f). Record residual risks.

### 8.c — Coordinated backup maintenance profile

Complete `scripts/backup.sh` (run via `--profile maintenance`): brief cross-store
quiesce → `pg_dump` custom-format + `pg_dumpall --globals-only` → Qdrant collection
snapshot + alias/active-profile mapping → artifact inventory of artifacts referenced
by the dump → SHA-256 checksums + machine-readable manifest → verify locally → mark
the set complete only after all steps succeed. Retention prune (§11.6) as a separate,
safe step. `scripts/verify-backup.sh` validates a set's checksums + manifest.

Tests (integration, no live NAS needed): a backup run over a small seeded stack
produces a complete, checksum-verified set with the manifest fields (§11.5);
retention keeps the right counts; an interrupted run leaves no `completed` marker.

### 8.d — Backup/restore & lifecycle guides

Extend `docs/operations/`: **backup-restore** (full procedure + the empty-volume
restore drill), **PostgreSQL PITR** (optional; WAL archiving trade-offs),
**upgrade/migration** (Alembic `upgrade head`, image bump, Qdrant/embedding-profile
rebuild), **model setup** (pull the Ollama model; FastEmbed cache), and
**troubleshooting** (common failures + the runbook). Cross-link the DoD.

### 8.e — Provider operations

`docs/operations/provider-operations.md`: enable a provider (Ollama local; OpenAI
external opt-in), **key rotation** (Docker secret swap + restart), **external-data
review** (what the boundary counters attest; how to audit egress), **rate-limit /
cost control** (bounds: max evidence/output tokens, timeouts), and **incident
disable** (flip `DOCMAN_EXTERNAL_LLM_ENABLED=false` / remove the allowlist / restart
→ search + local Ask keep working).

### 8.f — Threat-model tests

Automated tests asserting the boundary holds:
- **Secret leakage:** the OpenAI key never appears in Problem details, logs, API
  responses, or a backup manifest; `read_openai_api_key` is the only reader.
- **Accidental egress:** with external disabled/denied, Ask fails closed and no
  outbound provider request is attempted; local Ask + search still work.
- **Prompt injection:** an evidence chunk containing "ignore instructions / call a
  tool / reveal the system prompt" does not change the grounded behavior; citations
  stay server-owned; an invented citation path is dropped.
- **Path / metadata disclosure:** no endpoint returns `scan_root`; external payload
  counters keep metadata (paths/file names/tags/catalog ids) at zero; error
  responses carry no host paths or document text.

### 8.g — NAS external-backup workflow

Wire the **completed** backup directory to the NAS copy flow (§11.3): stage locally
→ copy to NAS `backups/incoming/<id>/` → verify NAS-side checksums → atomic
same-share rename to `backups/completed/<id>/` → completion marker last; a mapped-
drive interruption leaves the set incomplete and uncounted. **Live Docker volume
internals are never exposed** — only the application-aware backup set crosses to the
NAS, written solely by the maintenance service. Detection test: a completed set is
recognizable/verifiable on the NAS side; a `.partial` set is not counted.

### 8.h — Performance measurements

Measure on a **representative synthetic local corpus** (reuse/extend
`test-data/synthetic/`): scan+index throughput (files/s, MB/s), embedding rate,
search P50/P95 latency, and Ask retrieval+generation timing (local Ollama). Record
in `docs/operations/performance.md` with the corpus size + hardware profile and any
tuning notes (batch size, worker concurrency). No hard SLO; establish a baseline.

### 8.i — Accessibility + browser workflow review

Review the UI (`frontend/`) for keyboard navigation, focus order, labels/ARIA on
forms and status, colour-contrast of the badges/states, and accessible status
announcements for streamed Ask + long-running jobs. Verify the primary workflows in
a browser (add location → scan → search → ask → duplicates/coverage/sync). Record
findings + fixes in `docs/operations/accessibility.md`.

### DoD — end-to-end release proof (exit criteria)

- **README DoD E2E:** an integration/E2E test (or scripted drill) proving items 1–7
  (add location → index PDF/txt → local Ask with citations → re-index a change →
  status/failures).
- **Restore drill:** `scripts/restore.sh` into **empty** PostgreSQL + Qdrant volumes,
  then `catalog_consistency_check` + a known-query search returns expected evidence
  (§11.4). Fresh-install (`bootstrap.sh`) + NAS external-copy detection tested.
- **Known limits doc:** `docs/operations/known-limitations.md` (OCR deferred, no
  sync execution, external-embeddings deferred, single active embedding profile,
  no history retention, mapped-drive caveats, etc.).

---

## Security posture (must hold throughout)

- Provider secrets only via Docker secrets into the **API** service; never in the
  browser, worker, DB, logs, backups, or diagnostics (tested in 8.f).
- No arbitrary user-supplied path reads; responses expose only `display_path`.
- Document text is untrusted evidence; no execution derives from it.
- External egress only after the deployment opt-in + allowlist + secret + every
  evidence source `allow` + explicit acknowledgment; incident-disable is one flag.
- Backups contain application data + checksums only — no secrets; the NAS receives
  only the completed set, never live volume internals.

## Open decisions (resolve during 8.a / 8.c / 8.h)

1. **How much of the backup/restore + NAS flow is automatically tested** vs a
   documented manual drill. Leaning: automate the local backup set + checksum +
   retention + empty-volume restore consistency; document the live-NAS copy drill
   (no NAS in CI).
2. **Cleanup GC mechanism** — a scheduled `catalog_maintenance` job tick vs.
   documented manual maintenance commands. Leaning: a small durable GC job the
   scheduler can enqueue, plus a manual endpoint/CLI.
3. **Performance corpus + environment** — synthetic corpus size and whether numbers
   are captured in CI (unstable) or a documented local run. Leaning: a documented
   local run with the synthetic corpus; record method + baseline, not a CI gate.
4. **Accessibility depth** — self-review checklist vs. an automated axe pass. Leaning:
   a checklist + fixes now; automated a11y testing is a follow-up.
5. **PITR scope** — document optional WAL-archiving PITR only, or wire a minimal
   config. Leaning: document only (nightly logical dump is the MVP authority).

## New dependencies

- None required for the core. Possibly a dev-only a11y/testing helper for 8.i and
  a lightweight timing harness for 8.h (kept out of the runtime image).

## Ops notes

- Backups and restores run through the **maintenance** compose profile, never the
  always-on services; the NAS backup path is mounted only there.
- Live PostgreSQL/Qdrant volumes must never touch the mapped NAS drive (§11.3); the
  restore drill uses fresh local named volumes.
- The vector index is rebuildable — a missing/incompatible Qdrant snapshot does not
  block recovery; the consistency checker + reindex path restore it.
