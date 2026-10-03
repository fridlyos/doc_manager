#!/usr/bin/env bash
# Restore a completed backup set into an EMPTY PostgreSQL database (Phase 8 DoD).
#
# Thin wrapper around `python -m doc_manager.restore run <id>`. The Python
# orchestrator owns the discipline: fail-closed precheck (COMPLETED marker +
# SHA-256 checksums + readable manifest), then `psql` globals + `pg_restore` the
# custom dump. This wrapper only fails fast on the obvious preconditions.
#
# Restore PostgreSQL first; then rebuild the vector index (the index is NOT in the
# set — it is rebuilt from the catalog + live sources):
#   docker compose up -d
#   curl -X POST 'http://127.0.0.1:8000/api/v1/system/reindex?rebuild_vectors=true'
#   docker compose --profile maintenance run --rm backup /scripts/verify-consistency.sh
#
# Run via the maintenance profile (reuses the backup service: it has pg_restore,
# the DB URL, and depends on a healthy postgres):
#   docker compose --profile maintenance run --rm backup /scripts/restore.sh <backup-id>
set -euo pipefail

backup_id="${1:-}"
BACKUPS="${DOCMAN_BACKUP_ROOT:-/backups}"

if [[ -z "$backup_id" ]]; then
  echo "usage: restore.sh <backup-id>"
  echo "available completed sets:"
  ls -1 "$BACKUPS/completed" 2>/dev/null || echo "  (none)"
  exit 2
fi

set_dir="$BACKUPS/completed/$backup_id"
[[ -d "$set_dir" ]] || { echo "[FAIL] no completed set: $set_dir" >&2; exit 1; }
[[ -f "$set_dir/COMPLETED" ]] || { echo "[FAIL] set has no completion marker (not restorable)" >&2; exit 1; }

exec python -m doc_manager.restore run "$backup_id"
