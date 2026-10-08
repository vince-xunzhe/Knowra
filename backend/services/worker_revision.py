"""Non-secret runtime identity for gracefully replacing managed workers."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from path_utils import PROJECT_ROOT


def runtime_revision(root: Path = PROJECT_ROOT):
    digest = hashlib.sha256()
    for directory in ('backend', 'model_gateway'):
        for parent, dirs, files in os.walk(root / directory):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in ('tests', '__pycache__'))
            for name in sorted(files):
                if name.endswith('.py'):
                    path = Path(parent) / name
                    digest.update(str(path.relative_to(root)).encode())
                    digest.update(path.read_bytes())
    for value in (sys.executable, str(root), *(os.environ.get(key, '') for key in
                  ('PATH', 'CODEX_CLI_PATH', 'PYTHONPATH'))):
        digest.update(b'\0' + value.encode())
    return digest.hexdigest()
