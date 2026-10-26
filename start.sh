#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/native-common.sh"
case "${1:-}" in ""|--check) ;; *) echo "Usage: ./start.sh [--check]" >&2; exit 2;; esac
[ "${KNOWLEDGE_WIKI_MODE:-native}" = native ] || { echo "Native-only launcher; Docker is a separate explicit workflow. See INSTALL.md." >&2; exit 2; }
check_installed
BACKEND_PORT="${BACKEND_PORT:-8103}"
FRONTEND_PORT="${FRONTEND_PORT:-5183}"
"$VENV_DIR/bin/python" "$PROJECT_DIR/scripts/native_runtime.py" preflight "$PROJECT_DIR" "$BACKEND_PORT" "$FRONTEND_PORT"

if command -v lsof >/dev/null; then
  for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "Port $port is occupied; choose explicit worktree ports." >&2; exit 1
    fi
  done
fi
echo "Python: $VENV_DIR/bin/python"
if [ "${1:-}" = --check ]; then
  echo "Preflight passed: API=127.0.0.1:$BACKEND_PORT; Web=127.0.0.1:$FRONTEND_PORT. No services were started."
  exit 0
fi
"$VENV_DIR/bin/python" "$PROJECT_DIR/scripts/native_runtime.py" startup "$PROJECT_DIR" "$BACKEND_PORT" "$FRONTEND_PORT"
echo "Ready: worktree data=$PROJECT_DIR/data; API=127.0.0.1:$BACKEND_PORT; Web=127.0.0.1:$FRONTEND_PORT"
cleanup() {
  local pid
  for pid in "${BACKEND_PID:-}" "${FRONTEND_PID:-}"; do
    [ -z "$pid" ] || kill "$pid" 2>/dev/null || true
  done
  # Keep the launcher/terminal alive until its own services finish shutdown.
  # The API's existing shutdown hooks can then stop its recommendation worker.
  # Do not kill independent task workers or other checkouts' processes.
  for pid in "${BACKEND_PID:-}" "${FRONTEND_PID:-}"; do
    [ -z "$pid" ] || wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT
trap 'exit 130' INT TERM
export KNOWRA_DEPLOY_MODE=local
export KNOWRA_TASK_DB="${KNOWRA_TASK_DB:-$PROJECT_DIR/data/tasks.db}"
backend_args=(main:app --host 127.0.0.1 --port "$BACKEND_PORT" --timeout-graceful-shutdown 10)
[ "${BACKEND_RELOAD:-0}" = 1 ] && backend_args+=(--reload)
if [ "${KNOWRA_LOAD_ENV:-0}" = 1 ]; then
  [ -f "$PROJECT_DIR/.env" ] || { echo "KNOWRA_LOAD_ENV=1 requires this worktree's .env" >&2; exit 1; }
  backend_args+=(--env-file "$PROJECT_DIR/.env")
fi
echo "API startup may initialize/migrate this worktree's data and start its independent worker."
echo "Stopping this launcher waits for API/Web shutdown; independent task workers may continue. See docs/TASK-WORKERS.md."
cd "$PROJECT_DIR/backend"
"$VENV_DIR/bin/python" -m uvicorn "${backend_args[@]}" &
BACKEND_PID=$!
cd "$PROJECT_DIR/frontend"
VITE_API_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort &
FRONTEND_PID=$!
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do sleep 1; done
exit 1
