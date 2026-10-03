#!/usr/bin/env bash
# Post-restore SQL<->vector consistency gate (Phase 8 DoD).
#
# Thin wrapper around `python -m doc_manager.restore verify-consistency`, which
# scans the catalog's chunks against the Qdrant points for the active embedding
# profile and exits 0 only when there is no drift (no missing/orphan points).
# Run after `restore.sh` + the `rebuild_vectors` reindex.
#
#   docker compose --profile maintenance run --rm backup /scripts/verify-consistency.sh
set -euo pipefail

exec python -m doc_manager.restore verify-consistency
