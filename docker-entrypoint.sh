#!/usr/bin/env bash
# Run both backscatter processes (FastAPI server + collect loop) in one container.
# Lean by design — no supervisord. If either process exits, this entrypoint exits, so
# the container reflects the failure (compose `restart` brings the whole thing back)
# rather than silently running half-up.
set -euo pipefail

serve=""
collect=""
term() {
  # Forward shutdown to both children; collect handles SIGTERM (interruptible stop),
  # uvicorn shuts down gracefully.
  kill -TERM "$serve" "$collect" 2>/dev/null || true
}
trap term TERM INT

# The served (and published) port — one value, from the environment.
port="${BACKSCATTER_PORT:-8085}"

# Server first. create_app() bootstraps + seeds the SQLite store synchronously on
# startup, so by the time the API answers, the DB exists and locations are seeded.
backscatter serve --host 0.0.0.0 --port "$port" &
serve=$!

# Wait for the API before starting collect — this staggering avoids the empty-DB seed
# race when both processes hit a fresh mounted volume at the same instant.
for _ in $(seq 1 60); do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${port}/api/config')" 2>/dev/null; then
    break
  fi
  # Bail early if the server died during startup.
  kill -0 "$serve" 2>/dev/null || { echo "server exited during startup" >&2; wait "$serve"; exit 1; }
  sleep 1
done

backscatter collect &
collect=$!

# Return as soon as EITHER process exits, then take the container down.
wait -n "$serve" "$collect"
code=$?
term
wait || true
exit "$code"
