#!/bin/bash
set -e

# Start Valkey on loopback only; suppress verbose startup logs
valkey-server --bind 127.0.0.1 --port 6379 --loglevel warning &
VALKEY_PID=$!

# PONG readiness loop: up to 50 retries x 0.1s = 5s maximum wait
RETRIES=0
until valkey-cli -p 6379 ping 2>/dev/null | grep -q PONG; do
    RETRIES=$((RETRIES + 1))
    if [ "$RETRIES" -ge 50 ]; then
        echo "Valkey failed to start after 5s - aborting" >&2
        exit 1
    fi
    sleep 0.1
done
echo "Valkey ready (PID ${VALKEY_PID})"

# Crash watcher: if Valkey exits unexpectedly, send SIGTERM to Python (PID 1)
# so CF detects the crash and schedules a container restart.
# Uses kill -0 polling because wait cannot wait on a sibling process from a subshell.
( while kill -0 "$VALKEY_PID" 2>/dev/null; do sleep 1; done
  echo "Valkey exited unexpectedly - triggering container restart" >&2
  kill -TERM 1 2>/dev/null || true
  sleep 5
  kill -KILL 1 2>/dev/null || true
) &

# Runtime-computed vars (not baked into the image ENV block)
# CF sets $PORT dynamically; OCI leaves it unset so we fall back to 8765
export NETRA_SERVER__PORT="${NETRA_SERVER__PORT:-${PORT:-8765}}"
export NETRA_SERVER__SESSION_BACKEND="${NETRA_SERVER__SESSION_BACKEND:-valkey}"
export NETRA_VALKEY__URL="${NETRA_VALKEY__URL:-redis://127.0.0.1:6379/0}"

exec /app/.venv/bin/python /app/server.py
