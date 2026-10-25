#!/usr/bin/env bash
# Shared preflight only: no application imports or credential loading.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/backend/.venv"
export UV_CACHE_DIR="$PROJECT_DIR/.cache/uv"
export npm_config_cache="$PROJECT_DIR/.cache/npm"
check_paths() {
  "$1" - "$PROJECT_DIR" <<'PATHS'
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
for relative in ("backend", "backend/.venv", "frontend", "frontend/node_modules", ".cache", ".cache/uv", ".cache/npm", ".cache/npm-user.npmrc", ".cache/npm-global.npmrc", ".cache/native-install.json"):
    p = root / relative
    assert not p.is_symlink() and p.resolve().is_relative_to(root), f"Refusing linked/outside worktree path: {relative}"
PATHS
}
check_node() {
  command -v node >/dev/null && command -v npm >/dev/null || { echo "Node 24.14.1+ (24.x) and npm are required." >&2; return 1; }
  node -e 'const [a,b,c]=process.versions.node.split(".").map(Number); if(a!==24 || b<14 || (b===14 && c<1)) { console.error("Use Node 24.14.1+ within 24.x; see .node-version"); process.exit(1) }'
}
check_python() {
  "$1" -c 'import sys; assert sys.version_info[:2] == (3,12) and sys.version_info >= (3,12,13), "Use Python 3.12.13+ within 3.12.x"'
}
check_installed() {
  check_paths "${PYTHON_BIN:-python3}" || return 1
  check_node || return 1
  [ -x "$VENV_DIR/bin/python" ] || { echo "Run ./scripts/install-native.sh first." >&2; return 1; }
  check_python "$VENV_DIR/bin/python" || return 1
  "$VENV_DIR/bin/python" - "$PROJECT_DIR" <<'CHECK'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
assert pathlib.Path(sys.prefix).resolve() == root / "backend/.venv", "Use this worktree's own virtual environment"
marker = root / ".cache/native-install.json"
assert marker.is_file(), "Run ./scripts/install-native.sh first"
record = json.loads(marker.read_text())
for relative in ("backend/requirements-dev.lock", "frontend/package-lock.json"):
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == record[relative], f"{relative} changed; rerun install-native.sh"
assert (root / "frontend/node_modules/.package-lock.json").is_file(), "Frontend dependencies are missing"
CHECK
}
