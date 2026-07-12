# Phase 1 operations runbook

Local stack: PostgreSQL, Qdrant, the FastAPI API, and the worker on Windows
Docker Desktop with the WSL 2 Linux engine. This runbook covers the one-command
start, supported platform, storage rules, and the mapped-drive validation that
Phase 1 must prove.

## Prerequisites

- Windows with a current WSL 2 kernel.
- Docker Desktop using the **WSL 2 Linux engine** and **Linux containers**.
  Record the Docker Desktop and Compose versions and pin the minimum supported
  versions before first production data.
- The mapped NAS drive (if used) connected in the **same Windows logon context**
  that runs Docker Desktop. Drive letters are per-logon; a mapping created under
  another account may be invisible to Compose.

## One command to start

```bash
make up          # copies .env if missing, builds images, starts the stack
make ps          # service status
make logs        # tail api + worker
make down        # stop (keeps volumes)
```

`make up` is `docker compose up -d --build`. With defaults it works without a
NAS: the worker mounts the in-repo synthetic corpus read-only.

Start the dev UI as well with `make up-dev` (Vite on `127.0.0.1:5173`). In
production the API container serves the built static assets instead.

## Preflight before scanning or backing up

```bash
make preflight   # scripts/check.sh
```

Verifies `.env`, that the source root exists and carries the expected sentinel,
and that the backup destination is writable. A missing sentinel or unreadable
mount must stop a scan — the worker marks the location `unavailable` rather than
treating an empty directory as "all files deleted".

## Storage rules (non-negotiable)

- PostgreSQL and Qdrant live data use the Docker-managed named volumes
  `postgres_data` and `qdrant_data` on local SSD.
- **Never** bind-mount live PostgreSQL/Qdrant data to the mapped NAS/SMB drive.
  Giving an SMB path a Docker volume name does not change its filesystem
  semantics; it remains unsupported for live database data.
- PostgreSQL page checksums are enabled at first cluster init
  (`POSTGRES_INITDB_ARGS=--data-checksums`). Changing this requires recreating
  the `postgres_data` volume.
- Qdrant runs its own startup filesystem-compatibility check. It is a required
  preflight and **must not be bypassed** for production. If Qdrant refuses the
  storage, fix the storage — do not override the check.
- The writable NAS backup path is mounted only into the `backup` maintenance
  service, never into api/worker/postgres/qdrant.

## Network exposure

- Only the API publishes a port, bound to `127.0.0.1` (and the dev UI under the
  `dev` profile). PostgreSQL and Qdrant are reachable only on the internal
  compose network — no database or model service is internet-exposed by default.
- LAN exposure and authentication are out of Phase 1 scope; add auth/TLS before
  exposing the service to other users.

## Health and readiness

- `GET /health/live` — process is up (Docker liveness check).
- `GET /health/ready` — returns 200 only when **required** services (PostgreSQL,
  Qdrant) are up; returns 503 otherwise. Optional generation providers (Ollama,
  OpenAI) being down or disabled does not fail readiness; the system reports
  `search_only: true` instead.
- `GET /api/v1/system/status` — the same component report for the UI.

## Mapped-drive acceptance test (Phase 1 exit criterion)

Prove the mapped document and backup drives survive a reboot:

1. `make up`, then confirm the worker can read `/sources/nas` and the backup
   probe passes (`make preflight`).
2. Reboot Windows. Start Docker Desktop.
3. `make up` again. Re-run `make preflight`.
4. Confirm the source mount is still readable and the backup path still writable
   from the same Docker Desktop/Compose context.

If drive-letter binding proves unreliable, the documented fallback is a direct
read-only CIFS Docker volume for documents plus a separate CIFS backup volume,
using a least-privilege NAS account — subject to a credential-exposure review.
It still must not be used for live PostgreSQL or Qdrant data.
