#!/usr/bin/env bash
# Verify a completed backup set's integrity — PHASE 1 SKELETON.
# Checks presence of a completion marker + manifest; full checksum verification
# and a restore-into-throwaway-volumes drill land in Phase 8.
set -euo pipefail

backup_id="${1:-}"
BACKUPS="${DOCMAN_BACKUP_ROOT:-/backups}"
[[ -n "$backup_id" ]] || { echo "usage: verify-backup.sh <backup-id>"; exit 2; }

set_dir="$BACKUPS/completed/$backup_id"
[[ -d "$set_dir" ]] || { echo "[FAIL] no completed set: $set_dir"; exit 1; }

status=0
[[ -f "$set_dir/COMPLETED" ]] && echo "[ ok ] completion marker" || { echo "[FAIL] missing completion marker"; status=1; }
[[ -f "$set_dir/manifest.json" ]] && echo "[ ok ] manifest present" || { echo "[FAIL] missing manifest"; status=1; }

echo "[TODO Phase 8] verify SHA-256 checksums of every artifact"
exit "$status"
