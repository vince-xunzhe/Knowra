"""User-driven multitenant migration runner.

This is the **only** sanctioned way to apply the SQLite → multi-tenant
UUID migration to a real database. Running it is a one-way change; the
companion SQLAlchemy models + several routers must be updated to
String-id awareness before the app can boot against the migrated DB
(W3.2 — not yet done at the time of writing).

Usage::

    # Dry-run: open a temporary copy of the DB, migrate it, run the
    # verification report, then throw the result away. Use this to see
    # exactly what the migration would do without touching the original.
    backend/.venv/bin/python -m backend.scripts.migrate_multitenant --dry-run

    # Real run. Writes a timestamped backup next to the DB before
    # touching anything; aborts unless --confirm is passed (so an
    # accidental Tab-completion can't nuke the data).
    backend/.venv/bin/python -m backend.scripts.migrate_multitenant --confirm

    # Custom user_id (defaults to env KNOWRA_LOCAL_USER_ID or the
    # all-zero UUID).
    backend/.venv/bin/python -m backend.scripts.migrate_multitenant --confirm --user-id=<uuid>
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Make the backend importable when run as a module from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text

from multitenant_migration import (
    DEFAULT_LOCAL_USER_ID,
    is_multitenant_migrated,
    migrate_to_multitenant,
    verify_post_migration,
)


def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "knowledge.db"


def _backup_path(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return db_path.with_name(f"{db_path.stem}.pre-multitenant.{stamp}{db_path.suffix}")


def _row_counts(engine) -> dict[str, int]:
    out = {}
    with engine.connect() as conn:
        for t in ("papers", "knowledge_nodes", "knowledge_edges", "llm_calls"):
            exists = conn.execute(
                text(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"
                ),
                {"n": t},
            ).fetchone()
            if not exists:
                out[t] = 0
                continue
            out[t] = int(conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar())
    return out


def _print_report(title: str, report: dict) -> None:
    print(f"\n── {title} ──")
    print(f"  id types:        {report['id_types']}")
    print(f"  row counts:      {report['row_counts']}")
    print(f"  user_id all set: {report['all_user_id_set']}")
    if "orphan_edges" in report:
        print(f"  orphan edges:    {report['orphan_edges']}")
    if "orphan_node_refs" in report:
        print(f"  orphan node refs: {report['orphan_node_refs']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=_default_db_path(),
                        help="Path to the SQLite DB (default: data/knowledge.db)")
    parser.add_argument("--user-id", default=os.environ.get(
        "KNOWRA_LOCAL_USER_ID", DEFAULT_LOCAL_USER_ID
    ), help="UUID to stamp on every existing row")
    parser.add_argument("--dry-run", action="store_true",
                        help="Migrate a temp copy of the DB and discard")
    parser.add_argument("--confirm", action="store_true",
                        help="Required to write to the real DB")
    parser.add_argument("--no-backup", action="store_true",
                        help="Skip the timestamped backup (NOT recommended)")
    args = parser.parse_args()

    db_path: Path = args.db
    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}", file=sys.stderr)
        return 2

    if args.dry_run:
        # Copy DB to a temp location and run migration there.
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            tmp_path = Path(tf.name)
        try:
            shutil.copy2(db_path, tmp_path)
            print(f"[dry-run] working copy: {tmp_path}")
            engine = create_engine(f"sqlite:///{tmp_path}")
            with engine.connect() as conn:
                before = _row_counts(engine)
            with engine.begin() as conn:
                if is_multitenant_migrated(conn):
                    print("[dry-run] DB already migrated — nothing to do")
                    return 0
                counts = migrate_to_multitenant(conn, user_id=args.user_id)
                report = verify_post_migration(conn)
            print(f"\n[dry-run] migrated rows: {counts}")
            print(f"[dry-run] row counts before: {before}")
            _print_report("post-migration report", report)
            return 0
        finally:
            tmp_path.unlink(missing_ok=True)

    if not args.confirm:
        print("ERROR: refusing to write without --confirm", file=sys.stderr)
        print("       Run --dry-run first to preview the change.", file=sys.stderr)
        return 2

    # Real migration path
    backup: Path | None = None
    if not args.no_backup:
        backup = _backup_path(db_path)
        shutil.copy2(db_path, backup)
        print(f"backup written: {backup}")

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        if is_multitenant_migrated(conn):
            print("DB already migrated — nothing to do")
            return 0
        before = _row_counts(engine)

    try:
        with engine.begin() as conn:
            counts = migrate_to_multitenant(conn, user_id=args.user_id)
            report = verify_post_migration(conn)
    except Exception as exc:
        print(f"\nERROR: migration failed and was rolled back: {exc}", file=sys.stderr)
        if backup:
            print(f"      Backup is at {backup}; restore with `cp` if needed.")
        return 1

    print(f"\nmigrated rows: {counts}")
    print(f"row counts before: {before}")
    _print_report("post-migration report", report)
    print("\n⚠️  Next steps before restarting the app (W3.2):")
    print("    1. Update backend/models.py: change `id`, `source_id`, ")
    print("       `target_id` columns from Integer → String, add `user_id` + ")
    print("       `legacy_id` columns to each main model.")
    print("    2. Update routers that declare `paper_id: int` / `node_id: int` ")
    print("       path parameters to use `str`.")
    print("    3. Re-run the test suite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
