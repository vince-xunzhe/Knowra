#!/usr/bin/env bash
set -uo pipefail
if [ "$#" -gt 1 ]; then echo "Usage: ./scripts/check.sh [--real-start]" >&2; exit 2; fi
case "${1:-}" in ""|--real-start) ;; *) echo "Usage: ./scripts/check.sh [--real-start]" >&2; exit 2;; esac
source "$(dirname "$0")/native-common.sh"
check_installed || exit 1
cd "$PROJECT_DIR"
result=0
run() { echo "+ $*"; "$@" || result=1; }
run "$VENV_DIR/bin/python" scripts/test_native_startup.py
run "$VENV_DIR/bin/python" scripts/check_backend.py
run "$VENV_DIR/bin/python" scripts/check_frontend.py
if [ "${1:-}" = --real-start ]; then
  if [ "$result" -eq 0 ]; then
    run "$VENV_DIR/bin/python" scripts/check_native_start.py
  else
    echo "Real startup smoke not run because prerequisite checks failed." >&2
  fi
fi
exit "$result"
