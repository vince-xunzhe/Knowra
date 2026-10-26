#!/usr/bin/env bash
# Shared preflight only: no application imports or credential loading.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="$PROJECT_DIR/.cache/uv"
export npm_config_cache="$PROJECT_DIR/.cache/npm"
BOOTSTRAP_PYTHON="${PYTHON_BIN:-python3}"
runtime() {
  command -v "$BOOTSTRAP_PYTHON" >/dev/null || { echo "Knowra: Python executable not found: $BOOTSTRAP_PYTHON. Set PYTHON_BIN=/path/to/python3.12 and run ./scripts/install-native.sh." >&2; return 1; }
  "$BOOTSTRAP_PYTHON" "$PROJECT_DIR/scripts/native_runtime.py" "$@"
}
check_paths() { runtime paths "$PROJECT_DIR"; }
select_venv() { VENV_DIR="$(runtime select "$PROJECT_DIR")"; }
check_python() { runtime python "$1"; }
check_node() {
  if [ -n "${NODE_BIN:-}" ]; then
    [ -x "$NODE_BIN" ] || { echo "Knowra: NODE_BIN is not executable: $NODE_BIN" >&2; return 1; }
    [ "$(basename "$NODE_BIN")" = node ] || { echo "Knowra: NODE_BIN must name the Node executable as /path/to/node24/bin/node, not an alias or wrapper named differently." >&2; return 1; }
    NODE_BIN="$(cd "$(dirname "$NODE_BIN")" && pwd)/node"
    export PATH="$(dirname "$NODE_BIN"):$PATH"
  fi
  command -v node >/dev/null && command -v npm >/dev/null || { echo "Knowra: Node 24.14.1+ (24.x) and npm are required. Use NODE_BIN=/path/to/node24/bin/node for this command." >&2; return 1; }
  node -e 'const [a,b,c]=process.versions.node.split(".").map(Number); if(a!==24 || b<14 || (b===14 && c<1)) { console.error(`Knowra: Found Node ${process.versions.node} at ${process.execPath}; requires Node 24.14.1+ within 24.x. Select it with NODE_BIN=/path/to/node24/bin/node (no global change).`); process.exit(1) }'
}
check_installed() {
  select_venv || return 1
  if [ ! -x "$VENV_DIR/bin/python" ]; then runtime installed "$PROJECT_DIR"; return 1; fi
  check_python "$VENV_DIR/bin/python" >/dev/null || return 1
  "$VENV_DIR/bin/python" "$PROJECT_DIR/scripts/native_runtime.py" installed "$PROJECT_DIR" || return 1
  check_node || return 1
}
