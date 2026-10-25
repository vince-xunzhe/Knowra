#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/native-common.sh"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
check_node
check_python "$PYTHON_BIN"
check_paths "$PYTHON_BIN"
command -v uv >/dev/null || { echo "Install uv separately before using this installer." >&2; exit 1; }
mkdir -p "$PROJECT_DIR/.cache/uv" "$PROJECT_DIR/.cache/npm"
: > "$PROJECT_DIR/.cache/npm-user.npmrc"
: > "$PROJECT_DIR/.cache/npm-global.npmrc"
if [ -e "$VENV_DIR" ]; then
  check_python "$VENV_DIR/bin/python"
  "$VENV_DIR/bin/python" -c 'import pathlib,sys; assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve(), "Do not share environments"' "$VENV_DIR"
else
  env -i PATH="$PATH" UV_CACHE_DIR="$UV_CACHE_DIR" UV_PYTHON_DOWNLOADS=never uv --no-config venv --python "$PYTHON_BIN" "$VENV_DIR"
fi
# Official, hash-checked wheels only; no user indexes/config or source builds.
env -i PATH="$PATH" UV_CACHE_DIR="$UV_CACHE_DIR" UV_PYTHON_DOWNLOADS=never uv --no-config pip sync --python "$VENV_DIR/bin/python" --require-hashes --only-binary :all: --default-index https://pypi.org/simple "$PROJECT_DIR/backend/requirements-dev.lock"
env -i PATH="$PATH" npm_config_cache="$npm_config_cache" npm --prefix "$PROJECT_DIR/frontend" ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org --userconfig="$PROJECT_DIR/.cache/npm-user.npmrc" --globalconfig="$PROJECT_DIR/.cache/npm-global.npmrc"
# Optional fsevents is the only current install-script package. No lifecycle,
# xattr or ad-hoc codesign step is run; packaged native bindings remain intact.
env -i PATH="$PATH" UV_CACHE_DIR="$UV_CACHE_DIR" uv --no-config pip check --python "$VENV_DIR/bin/python"
"$VENV_DIR/bin/python" - "$PROJECT_DIR" <<'RECORD'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
paths = ("backend/requirements-dev.lock", "frontend/package-lock.json")
(root / ".cache/native-install.json").write_text(json.dumps({p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}, indent=2) + "\n")
RECORD
echo "Installed independent locked Web dependencies; no app or migration started."
