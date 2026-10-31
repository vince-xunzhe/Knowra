"""Killable local file hasher used by the bounded Ask revision check."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    try:
        paths = json.loads(sys.stdin.read())
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError("invalid hash request")
        result: dict[str, list[str]] = {}
        for raw in paths:
            sha256 = hashlib.sha256()
            md5 = hashlib.md5(usedforsecurity=False)
            with Path(raw).open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    sha256.update(chunk)
                    md5.update(chunk)
            result[raw] = [sha256.hexdigest(), md5.hexdigest()]
        sys.stdout.write(json.dumps(result, separators=(",", ":")))
        return 0
    except BaseException as error:
        sys.stdout.write(json.dumps({"error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
