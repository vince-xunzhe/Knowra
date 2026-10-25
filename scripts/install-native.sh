#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/native-common.sh"
# A bootstrap Python can be older than the application's supported runtime.
check_paths "$BOOTSTRAP_PYTHON"
select_venv
check_node
command -v uv >/dev/null || { echo "Knowra: Install uv separately before using this installer." >&2; exit 1; }
mkdir -p "$PROJECT_DIR/.cache/uv" "$PROJECT_DIR/.cache/npm"
if [ -n "${PYTHON_BIN:-}" ]; then
  INSTALL_PYTHON="$PYTHON_BIN"
elif command -v python3.12 >/dev/null; then
  INSTALL_PYTHON="$(command -v python3.12)"
else
  INSTALL_PYTHON="$(env -i PATH="$PATH" UV_CACHE_DIR="$UV_CACHE_DIR" UV_PYTHON_DOWNLOADS=never uv --no-config python find --no-python-downloads "$(cat "$PROJECT_DIR/.python-version")")" || {
    echo "Knowra: No installed Python 3.12.13+ found. Set PYTHON_BIN=/path/to/python3.12; no runtime was downloaded." >&2; exit 1;
  }
fi
INSTALL_VERSION="$(check_python "$INSTALL_PYTHON")"
echo "Installing native environment: $VENV_DIR (Python $INSTALL_VERSION); legacy environments are preserved."
runtime begin-install "$PROJECT_DIR" "$VENV_DIR" "$INSTALL_VERSION"
: > "$PROJECT_DIR/.cache/npm-user.npmrc"
: > "$PROJECT_DIR/.cache/npm-global.npmrc"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  env -i PATH="$PATH" UV_CACHE_DIR="$UV_CACHE_DIR" UV_PYTHON_DOWNLOADS=never uv --no-config venv --allow-existing --python "$INSTALL_PYTHON" "$VENV_DIR"
fi
check_python "$VENV_DIR/bin/python" >/dev/null
"$VENV_DIR/bin/python" "$PROJECT_DIR/scripts/native_runtime.py" owner "$PROJECT_DIR" "$VENV_DIR"
# Official, hash-checked wheels only; no user indexes/config or source builds.
env -i PATH="$PATH" UV_CACHE_DIR="$UV_CACHE_DIR" UV_PYTHON_DOWNLOADS=never uv --no-config pip sync --python "$VENV_DIR/bin/python" --require-hashes --only-binary :all: --default-index https://pypi.org/simple "$PROJECT_DIR/backend/requirements-dev.lock"
env -i PATH="$PATH" npm_config_cache="$npm_config_cache" npm --prefix "$PROJECT_DIR/frontend" ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org --userconfig="$PROJECT_DIR/.cache/npm-user.npmrc" --globalconfig="$PROJECT_DIR/.cache/npm-global.npmrc"
# Optional fsevents is the only current install-script package. No lifecycle,
# xattr or ad-hoc codesign step is run; packaged native bindings remain intact.
env -i PATH="$PATH" UV_CACHE_DIR="$UV_CACHE_DIR" uv --no-config pip check --python "$VENV_DIR/bin/python"
"$VENV_DIR/bin/python" "$PROJECT_DIR/scripts/native_runtime.py" finish-install "$PROJECT_DIR" "$VENV_DIR"

echo "Installed independent locked Web dependencies; no app or migration started."
