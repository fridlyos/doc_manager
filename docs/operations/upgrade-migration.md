# Upgrade and migration

How to move the stack to a new application version safely: run schema
migrations, bump images, and decide when a vector re-index is required. The
golden rule is **back up first** ([`backup-restore.md`](backup-restore.md)) and
apply changes in dependency order: schema → code/images → index.

## Before any upgrade

1. Take and verify a backup:
   ```bash
   make backup
   docker compose --profile maintenance run --rm backup /scripts/verify-backup.sh <backup-id>
   ```
2. Note the current image tags / git revision so you can roll back.
3. Read the target version's `PHASE<N>_STATUS.md` / release notes for any new
   migration or an embedding-profile change (which forces a re-index — see
   below).

## Database migrations (Alembic)

Migrations are **not** run automatically at container start. Apply them
explicitly after pulling new code and before serving traffic. Alembic is
configured in `backend/alembic.ini` (`script_location = migrations`); migration
scripts live in `backend/migrations/versions/` (`0001`–`0006` today). The backend
image has them at `/app/migrations`.

```bash
# From the running API container (WORKDIR /app):
docker compose exec api alembic upgrade head

# Inspect state:
docker compose exec api alembic current      # applied revision
docker compose exec api alembic history       # full chain
```

- On a **fresh database**, `alembic upgrade head` creates the whole schema. Run
  it once after first `make up`, before the first scan.
- Each phase has proven `upgrade`/`downgrade` round-trips, but **treat downgrade
  as a break-glass tool**, not a routine rollback — prefer restoring the
  pre-upgrade backup if a migration goes wrong.

## Upgrade sequence (zero data on the mapped drive at risk)

1. **Stop the writers cleanly.** `docker compose stop worker` lets in-flight jobs
   release to `retry_wait` within the shutdown grace (Phase 8.a graceful
   shutdown); the API can keep serving reads briefly if you prefer.
2. **Pull new code / images.** Update the checkout, then rebuild:
   ```bash
   docker compose build
   ```
3. **Run migrations** against the still-running (or freshly started) postgres:
   ```bash
   docker compose up -d postgres qdrant
   docker compose up -d api          # start api to get an /app shell
   docker compose exec api alembic upgrade head
   ```
4. **Start the rest** and confirm health:
   ```bash
   docker compose up -d
   curl -s http://127.0.0.1:8000/health/ready        # 200 when PG + Qdrant ready
   curl -s http://127.0.0.1:8000/api/v1/system/status # component report
   ```
5. **Re-index only if required** (next section).

Roll back by restoring the pre-upgrade backup and redeploying the previous image
tags; do not leave a partially-migrated schema running against old code.

## When a re-index is required

Embeddings and the vector index are local and tied to an **embedding profile**
(model + vector size + prefix scheme). The catalog records the profile so the
system knows when vectors are stale (README §"Re-indexing Strategy").

- **No re-index needed** for: app code/API changes, UI changes, switching the
  *generation* provider (Ollama ↔ OpenAI) — generation is independent of the
  index. Switching providers never requires re-indexing.
- **Full re-index required** when the **embedding model or vector size changes**
  (`DOCMAN_EMBEDDING_MODEL`, default `BAAI/bge-small-en-v1.5`). A new profile
  means existing vectors are incompatible.

Controlled rebuild (Phase 6 path):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/system/reindex            # rebuild under the active profile
curl -X POST http://127.0.0.1:8000/api/v1/system/remove-stale-vectors # retire the old profile's points
```

Run the retire step **after** the rebuild completes. Both are durable fan-out
jobs; watch progress via `GET /api/v1/system/status` and the jobs UI/API.

## Qdrant / PostgreSQL image bumps

- **PostgreSQL major upgrade:** a logical dump/restore across majors is the
  supported path (dump on the old major, restore into a fresh volume on the new
  major). The backup image's `pg_dump` is pinned to client-16; keep it `>=` the
  server major. Page checksums (`--data-checksums`) are set at cluster init and
  persist across the logical restore into a new volume.
- **Qdrant upgrade:** the index is rebuildable, so the safe path for an
  incompatible storage format is: take a backup, recreate the `qdrant_data`
  volume on the new image, then `POST /system/reindex` to rebuild from the
  catalog. A compatible in-place upgrade needs no re-index.

## Related

- [`backup-restore.md`](backup-restore.md) — back up before upgrading; restore to
  roll back.
- [`model-setup.md`](model-setup.md) — embedding/chat model setup and cache.
- [`troubleshooting.md`](troubleshooting.md) — migration and readiness failures.
