#!/usr/bin/env bash
# One-time local bootstrap: .env, backend deps, frontend deps, source sentinel.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== doc_manager bootstrap =="

# 1. .env
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "created .env from .env.example (review before starting the stack)"
fi

# 2. Backend deps
if command -v uv >/dev/null 2>&1; then
  echo "installing backend deps..."
  (cd backend && uv sync)
else
  echo "[warn] uv not found; skipping backend deps (see backend/README.md)"
fi

# 3. Frontend deps
if command -v npm >/dev/null 2>&1; then
  echo "installing frontend deps..."
  (cd frontend && npm install)
else
  echo "[warn] npm not found; skipping frontend deps"
fi

# 4. Source sentinel for the default (in-repo) synthetic corpus.
set -a; [[ -f .env ]] && . ./.env; set +a
SRC="${DOCMAN_NAS_DOCUMENTS_HOST_PATH:-./test-data/synthetic/source-roots}"
SENTINEL="${DOCMAN_NAS_MOUNT_SENTINEL:-.docman-source-id}"
if [[ -d "$SRC" && ! -f "$SRC/$SENTINEL" ]]; then
  echo "synthetic-corpus" > "$SRC/$SENTINEL"
  echo "wrote sentinel: $SRC/$SENTINEL"
fi

echo
echo "next: make preflight && make up"
