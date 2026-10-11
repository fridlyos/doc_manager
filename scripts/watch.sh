#!/usr/bin/env bash
# Dev watch loop: rebuild + restart the app whenever source changes.
#
# The Vite dev UI already hot-reloads (bind mount + HMR, see compose.override.yaml),
# so this watches the parts that DON'T auto-reload: the backend api/worker images
# and the deploy/compose files that define the stack. On a change it rebuilds the
# affected images and restarts them in place.
#
# Usage:
#   scripts/watch.sh            # watch backend + deploy + compose -> rebuild api,worker
#   scripts/watch.sh --all      # also rebuild the ui image on frontend changes
#                               # (only needed if you run the prod UI, not the dev profile)
#
# Watcher backend is auto-detected: watchexec > inotifywait (inotify-tools) >
# a portable polling fallback. Ctrl-C to stop.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=${COMPOSE:-docker compose}
DEBOUNCE=${WATCH_DEBOUNCE:-2}   # seconds to coalesce a burst of saves

SERVICES=(api worker)
WATCH_PATHS=(backend/src backend/pyproject.toml backend/alembic.ini deploy compose.yaml compose.override.yaml)

if [[ "${1:-}" == "--all" ]]; then
  SERVICES+=(ui)
  WATCH_PATHS+=(frontend/src frontend/package.json)
fi

rebuild() {
  echo
  echo "== change detected -> rebuild + restart: ${SERVICES[*]} =="
  # --build rebuilds the images; up recreates only what changed. Profiles are
  # included so ui is reachable when --all is used.
  if $COMPOSE --profile dev up -d --build "${SERVICES[@]}"; then
    echo "== up to date ($(date +%H:%M:%S)) =="
  else
    echo "== rebuild FAILED; leaving previous containers running =="
  fi
}

echo "doc_manager watch: services=[${SERVICES[*]}]"
echo "watching: ${WATCH_PATHS[*]}"
rebuild   # initial build so the stack is current before the first edit

if command -v watchexec >/dev/null 2>&1; then
  echo "watcher: watchexec"
  # Each change fires one rebuild; watchexec coalesces bursts and ignores VCS/
  # ignored paths. SERVICES is expanded now into the command it runs on change.
  watch_args=()
  for p in "${WATCH_PATHS[@]}"; do watch_args+=(--watch "$p"); done
  exec watchexec --debounce "${DEBOUNCE}s" "${watch_args[@]}" \
    -- $COMPOSE --profile dev up -d --build "${SERVICES[@]}"
elif command -v inotifywait >/dev/null 2>&1; then
  echo "watcher: inotifywait"
  while inotifywait -r -e modify,create,delete,move --quiet "${WATCH_PATHS[@]}" >/dev/null; do
    sleep "$DEBOUNCE"
    rebuild
  done
else
  echo "watcher: polling (install watchexec or inotify-tools for event-driven watching)"
  snapshot() { find "${WATCH_PATHS[@]}" -type f -not -path '*/node_modules/*' \
    -not -path '*/__pycache__/*' -printf '%T@ %p\n' 2>/dev/null | sort; }
  prev="$(snapshot)"
  while true; do
    sleep "$DEBOUNCE"
    cur="$(snapshot)"
    if [[ "$cur" != "$prev" ]]; then
      prev="$cur"
      rebuild
    fi
  done
fi
