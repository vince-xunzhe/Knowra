#!/usr/bin/env python3
"""Collapse duplicate local Paper rows and repair wiki references."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import database  # noqa: E402
from path_utils import DATA_DIR  # noqa: E402
from services.paper_dedupe_service import repair_duplicate_papers  # noqa: E402


def _backup() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = DATA_DIR / "backups" / f"paper-dedupe-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    for name in ("knowledge.db", "wiki_search.sqlite"):
        src = DATA_DIR / name
        if src.exists():
            shutil.copy2(src, backup_dir / name)

    for name in ("wiki", "paper_records"):
        src = DATA_DIR / name
        if src.exists():
            shutil.copytree(src, backup_dir / name)

    return backup_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Only report duplicate groups; do not mutate files")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup before mutating")
    args = parser.parse_args()

    if args.dry_run:
        db = database.SessionLocal()
        try:
            from services.paper_dedupe_service import _duplicate_groups  # noqa: PLC0415

            groups = _duplicate_groups(db)
            payload = {
                "duplicate_groups": len(groups),
                "duplicate_rows": sum(len(group) for group in groups),
                "groups": [
                    [
                        {
                            "id": paper.id,
                            "filename": paper.filename,
                            "filepath": paper.filepath,
                            "file_hash": paper.file_hash,
                            "processed_at": paper.processed_at.isoformat() if paper.processed_at else None,
                        }
                        for paper in group
                    ]
                    for group in groups
                ],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        finally:
            db.close()

    backup_dir = None if args.no_backup else _backup()
    db = database.SessionLocal()
    try:
        summary = repair_duplicate_papers(db)
    finally:
        db.close()

    # Now that duplicates are gone, install the uniqueness guards if possible.
    database.init_db()

    payload = summary.as_dict()
    payload["backup_dir"] = str(backup_dir) if backup_dir else None
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
