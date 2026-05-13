from __future__ import annotations

import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent


def ensure_project_root_on_path() -> None:
    project_root = str(PROJECT_ROOT)
    backend_root = str(BACKEND_DIR)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
