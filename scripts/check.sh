#!/usr/bin/env bash
# Storage and mount preflight (host-side). Verifies the mapped drive / source
# root and backup destination BEFORE the stack scans or backs up, so an
# accidentally-empty local directory is never reconciled against the catalog
# (TECHSTACK sections 10-11).
set -euo pipefail

fail=0
note() { printf '  %s\n' "$*"; }
ok()   { printf '  [ ok ] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; fail=1; }

cd "$(dirname "$0")/.."

echo "== doc_manager preflight =="

# .env present
if [[ -f .env ]]; then ok ".env present"; else bad ".env missing (run: make env)"; fi

# Load .env (only DOCMAN_ vars we need)
if [[ -f .env ]]; then set -a; . ./.env; set +a; fi

SRC="${DOCMAN_NAS_DOCUMENTS_HOST_PATH:-./test-data/synthetic/source-roots}"
SENTINEL="${DOCMAN_NAS_MOUNT_SENTINEL:-.docman-source-id}"
BACKUPS="${DOCMAN_NAS_BACKUPS_HOST_PATH:-./.backups}"

echo "-- source documents --"
if [[ -d "$SRC" ]]; then
  ok "source root present: $SRC"
  # Sentinel is REQUIRED for real mapped drives; for the in-repo synthetic
  # corpus it is advisory.
  if [[ -f "$SRC/$SENTINEL" ]]; then
    ok "sentinel present: $SRC/$SENTINEL"
  else
    note "[warn] sentinel '$SENTINEL' absent (required for mapped-drive roots)"
  fi
else
  bad "source root missing: $SRC (mapped drive not connected?)"
fi

echo "-- backup destination --"
if [[ -d "$BACKUPS" ]]; then
  probe="$BACKUPS/.docman-backup-probe.$$"
  if echo probe > "$probe" 2>/dev/null && [[ "$(cat "$probe")" == "probe" ]]; then
    rm -f "$probe"; ok "backup path writable: $BACKUPS"
  else
    rm -f "$probe" 2>/dev/null || true; bad "backup path not writable: $BACKUPS"
  fi
else
  note "[warn] backup path missing: $BACKUPS (only needed for the maintenance profile)"
fi

echo "-- container runtime --"
if command -v docker >/dev/null 2>&1; then
  ok "docker present: $(docker --version 2>/dev/null | head -1)"
else
  note "[warn] docker not found on PATH (needed to start the stack)"
fi

echo
if [[ "$fail" -ne 0 ]]; then
  echo "PREFLIGHT FAILED"; exit 1
fi
echo "PREFLIGHT OK"
