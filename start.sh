#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/.venv"

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  . "$PROJECT_DIR/.env"
  set +a
fi

RUN_MODE="${KNOWLEDGE_WIKI_MODE:-auto}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
BACKEND_PORT="${BACKEND_PORT:-}"
FRONTEND_PORT="${FRONTEND_PORT:-}"
NATIVE_CLEANED=0

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

find_free_port() {
  local port="$1"
  while lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; do
    port=$((port + 1))
  done
  echo "$port"
}

open_url() {
  local url="$1"
  [ "$OPEN_BROWSER" = "0" ] && return 0

  if command_exists open; then
    open "$url" >/dev/null 2>&1 || true
  elif command_exists xdg-open; then
    xdg-open "$url" >/dev/null 2>&1 || true
  elif command_exists cmd.exe; then
    cmd.exe /c start "$url" >/dev/null 2>&1 || true
  fi
}

schedule_open_url() {
  local url="$1"
  [ "$OPEN_BROWSER" = "0" ] && return 0

  (
    sleep 5
    open_url "$url"
  ) &
  OPENER_PID=$!
}

docker_compose_available() {
  if command_exists docker && docker compose version >/dev/null 2>&1; then
    return 0
  fi

  command_exists docker-compose
}

docker_daemon_available() {
  command_exists docker && docker info >/dev/null 2>&1
}

ensure_docker_daemon() {
  docker_daemon_available && return 0

  if command_exists colima; then
    echo "Starting Colima Docker runtime..."
    colima start --runtime docker >/dev/null
    docker_daemon_available
    return $?
  fi

  return 1
}

docker_compose() {
  if command_exists docker && docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

bad_node_path() {
  case "$1" in
    /Applications/*.app/*|*/Applications/*.app/*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

node_major() {
  "$1/node" -p "Number(process.versions.node.split('.')[0])" 2>/dev/null || echo 0
}

try_node_dir() {
  local node_dir="$1"
  [ -x "$node_dir/node" ] && [ -x "$node_dir/npm" ] || return 1

  local node_path
  node_path="$("$node_dir/node" -p "process.execPath" 2>/dev/null || true)"
  [ -n "$node_path" ] || node_path="$node_dir/node"

  if bad_node_path "$node_path"; then
    return 1
  fi

  local major
  major="$(node_major "$node_dir")"
  if [ "$major" -lt 20 ]; then
    return 1
  fi

  export PATH="$node_dir:$PATH"
  return 0
}

select_native_node() {
  local current_node_dir=""
  if command_exists node && command_exists npm; then
    current_node_dir="$(dirname "$(command -v node)")"
    try_node_dir "$current_node_dir" && return 0
  fi

  for node_dir in \
    /opt/homebrew/bin \
    /usr/local/bin \
    "$HOME/.volta/bin" \
    "$HOME/.asdf/shims"; do
    try_node_dir "$node_dir" && return 0
  done

  if [ -d "$HOME/.nvm/versions/node" ]; then
    local node_bin
    for node_bin in "$HOME"/.nvm/versions/node/*/bin; do
      try_node_dir "$node_bin" && return 0
    done
  fi

  return 1
}

clear_frontend_native_attrs() {
  if [ "$(uname -s)" = "Darwin" ] && [ -d "$FRONTEND_DIR/node_modules/@rolldown" ]; then
    echo "Preparing frontend native bindings..."
    find "$FRONTEND_DIR/node_modules/@rolldown" -name "*.node" -print0 | while IFS= read -r -d '' binding; do
      xattr -d com.apple.provenance "$binding" 2>/dev/null || true
      xattr -d com.apple.quarantine "$binding" 2>/dev/null || true
      if command_exists codesign; then
        codesign --force --sign - "$binding" >/dev/null 2>&1 || true
      fi
    done
  fi
}

cleanup_native() {
  [ "$NATIVE_CLEANED" = "1" ] && return 0
  NATIVE_CLEANED=1
  kill ${BACKEND_PID:-} ${FRONTEND_PID:-} ${OPENER_PID:-} 2>/dev/null || true
  echo "Stopped."
}

stop_native() {
  cleanup_native
  exit 130
}

run_docker() {
  export BACKEND_PORT="${BACKEND_PORT:-$(find_free_port 8000)}"
  export FRONTEND_PORT="${FRONTEND_PORT:-$(find_free_port 5173)}"

  mkdir -p "$PROJECT_DIR/data/artifacts" "$PROJECT_DIR/data/papers"

  echo "=== Knowledge Tree (Docker) ==="
  echo ""
  echo "Starting backend on http://localhost:$BACKEND_PORT"
  echo "Starting frontend on http://localhost:$FRONTEND_PORT"
  echo ""
  echo "Press Ctrl+C to stop both servers."
  echo ""

  schedule_open_url "http://localhost:$FRONTEND_PORT"
  cd "$PROJECT_DIR"
  docker_compose up --build
}

run_native() {
  if ! select_native_node; then
    echo "ERROR: Could not find a host Node.js >= 20 runtime outside an app bundle."
    echo "Install Docker for the fully isolated path, or install Node.js with Homebrew, nvm, Volta, or asdf."
    exit 1
  fi

  BACKEND_PORT="${BACKEND_PORT:-$(find_free_port 8000)}"
  FRONTEND_PORT="${FRONTEND_PORT:-$(find_free_port 5173)}"

  trap cleanup_native EXIT
  trap stop_native INT TERM

  echo "=== Knowledge Tree (native) ==="
  echo ""

  if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
  fi

  echo "Installing backend dependencies..."
  "$VENV_DIR/bin/python" -m pip install -q -r "$BACKEND_DIR/requirements.txt"

  echo "Installing frontend dependencies..."
  echo "Using Node $(node -v) from $(command -v node)"
  cd "$FRONTEND_DIR"
  npm install --silent
  clear_frontend_native_attrs

  echo ""
  echo "Starting backend on http://localhost:$BACKEND_PORT"
  echo "Starting frontend on http://localhost:$FRONTEND_PORT"
  echo ""
  echo "Press Ctrl+C to stop both servers."
  echo ""

  cd "$BACKEND_DIR"
  "$VENV_DIR/bin/uvicorn" main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload &
  BACKEND_PID=$!

  cd "$FRONTEND_DIR"
  VITE_API_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort &
  FRONTEND_PID=$!

  schedule_open_url "http://localhost:$FRONTEND_PORT"

  while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
    sleep 1
  done

  cleanup_native
  wait "$BACKEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" 2>/dev/null || true
  exit 1
}

case "$RUN_MODE" in
  auto)
    if docker_compose_available && ensure_docker_daemon; then
      run_docker
    else
      run_native
    fi
    ;;
  docker)
    if ! docker_compose_available; then
      echo "ERROR: Docker Compose is not installed."
      exit 1
    fi
    if ! ensure_docker_daemon; then
      echo "ERROR: Docker is installed but the daemon is not running."
      exit 1
    fi
    run_docker
    ;;
  native)
    run_native
    ;;
  *)
    echo "ERROR: Unknown KNOWLEDGE_WIKI_MODE=$RUN_MODE. Use auto, docker, or native."
    exit 1
    ;;
esac
