#!/usr/bin/env bash
# On-demand application-aware backup (TECHSTACK 11.5, Phase 8.c).
#
# Thin wrapper around `python -m doc_manager.backup run`. The Python orchestrator
# owns the discipline: build in local staging, checksum + manifest, verify, then
# atomically publish to completed/<id> and write the COMPLETED marker LAST, then
# prune per the GFS retention policy. An interrupted run never leaves a set that
# looks restorable. This wrapper only fails-fast on preconditions the module
# assumes: both roots present and the NAS destination writable.
#
# Run via the maintenance profile:
#   docker compose --profile maintenance run --rm backup /scripts/backup.sh
set -euo pipefail

STAGING="${DOCMAN_STAGING_ROOT:-/staging}"
BACKUPS="${DOCMAN_BACKUP_ROOT:-/backups}"

for d in "$STAGING" "$BACKUPS"; do
  [[ -d "$d" ]] || { echo "[FAIL] missing directory: $d" >&2; exit 1; }
done

# Backup destination write probe (never trust an unwritable/disconnected NAS).
probe="$BACKUPS/.docman-backup-probe.$$"
if ! ( echo probe > "$probe" && [[ "$(cat "$probe")" == "probe" ]] ); then
  rm -f "$probe" 2>/dev/null || true
  echo "[FAIL] backup destination not writable: $BACKUPS" >&2
  exit 1
fi
rm -f "$probe"

exec python -m doc_manager.backup run
