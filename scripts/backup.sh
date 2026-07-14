#!/usr/bin/env bash
# On-demand application-aware backup — PHASE 1 SKELETON.
#
# Phase 1 wires the maintenance profile, the local staging volume, and the
# NAS backup mount, and enforces the safety discipline: build in local staging,
# copy to backups/incoming/<id>/, verify, then rename to completed/<id>/ and
# write the completion marker LAST (TECHSTACK 11.5). The full implementation
# (advisory maintenance lock, pg_dump custom-format, pg_dumpall globals, native
# Qdrant snapshot download, artifact capture, manifest + checksums, retention)
# lands in Phase 8. This script fails loudly rather than producing a partial
# set that looks complete.
set -euo pipefail

STAGING="${DOCMAN_STAGING_ROOT:-/staging}"
BACKUPS="${DOCMAN_BACKUP_ROOT:-/backups}"
backup_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"

echo "== backup $backup_id (Phase 1 skeleton) =="

for d in "$STAGING" "$BACKUPS"; do
  [[ -d "$d" ]] || { echo "[FAIL] missing directory: $d"; exit 1; }
done

# Backup destination write probe (never trust an unwritable/disconnected NAS).
probe="$BACKUPS/.docman-backup-probe.$$"
if ! ( echo probe > "$probe" && [[ "$(cat "$probe")" == "probe" ]] ); then
  rm -f "$probe" 2>/dev/null || true
  echo "[FAIL] backup destination not writable: $BACKUPS"; exit 1
fi
rm -f "$probe"

stage_dir="$STAGING/$backup_id"
incoming="$BACKUPS/incoming/$backup_id"
completed="$BACKUPS/completed/$backup_id"
mkdir -p "$stage_dir" "$BACKUPS/incoming"

echo "staging at: $stage_dir"
echo "[TODO Phase 8] pg_dump --format=custom, pg_dumpall --globals-only"
echo "[TODO Phase 8] request + download native Qdrant collection snapshot"
echo "[TODO Phase 8] capture referenced immutable extracted-text artifacts"
echo "[TODO Phase 8] write manifest.json + SHA-256 checksums"

# Do NOT create a completed/ set or completion marker from the skeleton: an
# incomplete backup must never be counted as restorable.
echo
echo "SKELETON COMPLETE — no restorable backup set produced (implement in Phase 8)."
echo "incoming target would be: $incoming"
echo "completed target would be: $completed"
exit 0
