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

# Fail fast with a clear, actionable message if the archive directory isn't writable by
# us. The committed data/.gitkeep normally prevents this, but if ./data was created by
# root on the host (e.g. Docker auto-created the bind-mount source before it existed)
# the non-root container can't write the SQLite DB — which otherwise shows up as a
# cryptic "unable to open database file" crash-loop.
data_dir="${BACKSCATTER_DATA_DIR:-/data}"
if ! { mkdir -p "$data_dir" && touch "$data_dir/.write_test"; } 2>/dev/null; then
  uid="$(id -u)"; gid="$(id -g)"
  {
    echo "ERROR: cannot write to the archive directory ($data_dir)."
    echo "It is likely owned by root on the host, but this container runs as UID ${uid}."
    echo "Fix it once on the host (in the project folder), then start again:"
    echo "    sudo chown -R ${uid}:${gid} ./data"
    echo "    docker compose up -d"
  } >&2
  exit 1
fi
rm -f "$data_dir/.write_test"

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
