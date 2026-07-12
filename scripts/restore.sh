#!/usr/bin/env bash
# Restore from a completed backup set — PHASE 1 SKELETON.
#
# Restore authority order (TECHSTACK 11.4): PostgreSQL is authoritative; Qdrant
# is a rebuildable index. A missing/incompatible Qdrant snapshot must not block
# recovery — the collection can be rebuilt from PostgreSQL plus source documents
# or extracted-text artifacts. Full implementation lands in Phase 8.
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
[[ -d "$set_dir" ]] || { echo "[FAIL] no completed set: $set_dir"; exit 1; }
[[ -f "$set_dir/COMPLETED" ]] || { echo "[FAIL] set has no completion marker (not restorable)"; exit 1; }

echo "[TODO Phase 8] verify manifest + checksums for $backup_id"
echo "[TODO Phase 8] restore PostgreSQL logical dump into an empty database"
echo "[TODO Phase 8] restore or rebuild the Qdrant collection"
echo "[TODO Phase 8] verify catalog/vector consistency"
echo "SKELETON — no changes made."
