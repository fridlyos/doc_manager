#!/usr/bin/env bash
# Verify a completed backup set's integrity (TECHSTACK 11.5, Phase 8.c).
#
# Thin wrapper around `python -m doc_manager.backup verify <id>`, which requires
# the COMPLETED marker and recomputes the SHA-256 of every file in the manifest's
# SHA256SUMS. Exit 0 only when the whole set verifies.
#
#   docker compose --profile maintenance run --rm backup /scripts/verify-backup.sh <backup-id>
set -euo pipefail

backup_id="${1:-}"
[[ -n "$backup_id" ]] || { echo "usage: verify-backup.sh <backup-id>" >&2; exit 2; }

exec python -m doc_manager.backup verify "$backup_id"
