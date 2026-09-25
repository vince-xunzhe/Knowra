"""Consistent backups including committed data still in the WAL file."""
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote


def backup_sqlite(source: Path, destination: Path) -> None:
    uri = f"file:{quote(str(source.resolve()))}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as src:
        with closing(sqlite3.connect(str(destination))) as dst:
            src.backup(dst)
