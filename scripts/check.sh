#!/usr/bin/env bash
set -uo pipefail
source "$(dirname "$0")/native-common.sh"
check_installed || exit 1
cd "$PROJECT_DIR"
result=0
run() { echo "+ $*"; "$@" || result=1; }
run "$VENV_DIR/bin/python" scripts/check_backend.py
run "$VENV_DIR/bin/python" scripts/check_frontend.py
exit "$result"
