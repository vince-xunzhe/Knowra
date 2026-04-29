"""Phase 2A — SQLite FTS5 index over the LLM-compiled wiki layer.

Karpathy's "small and naive search engine over the wiki" — pure stdlib, zero
LLM tokens, indexes title + body of every wiki .md.

  index   : data/wiki_search.sqlite
  source  : data/wiki/papers/*.md + data/wiki/concepts/*.md
            (notes/ comes online in Phase 2C)
  ranking : FTS5 built-in bm25
  tokens  : trigram if available (handles CJK + English without external
            deps); else unicode61 as a fallback for older SQLite.

Rebuilds are full-table at this scale (~500 .md files = <1s on a laptop),
so we don't bother with incremental upserts. The index is rebuilt at app
startup, after every full wiki recompile, and after each per-paper auto-
compile in `_process_single`.
"""
import re
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional

from path_utils import DATA_DIR


DB_PATH = DATA_DIR / "wiki_search.sqlite"
WIKI_DIR = DATA_DIR / "wiki"


def _log(msg: str) -> None:
    print(f"[wiki_search] {msg}", file=sys.stderr, flush=True)


# --- table init -------------------------------------------------------------

_TOKENIZERS = ("trigram", "unicode61")


def _ensure_table(conn: sqlite3.Connection) -> str:
    """Idempotent. Returns the tokenizer name in use. Tries trigram first
    (best for CJK + English substring search); falls back to unicode61 if
    the SQLite build is too old (<3.34)."""
    cur = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wiki_fts'"
    )
    row = cur.fetchone()
    if row:
        sql = row[0] or ""
        for tok in _TOKENIZERS:
            if f"tokenize='{tok}'" in sql or f'tokenize="{tok}"' in sql:
                return tok
        return "unknown"

    last_err: Optional[Exception] = None
    for tok in _TOKENIZERS:
        try:
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE wiki_fts USING fts5(
                    kind UNINDEXED,
                    filename UNINDEXED,
                    path UNINDEXED,
                    title,
                    body,
                    compiled_at UNINDEXED,
                    tokenize='{tok}'
                )
                """
            )
            _log(f"FTS5 table created with tokenizer={tok}")
            return tok
        except sqlite3.OperationalError as e:
            last_err = e
            _log(f"tokenizer={tok} unavailable: {e}")
            # CREATE failed half-way? Drop and try next.
            try:
                conn.execute("DROP TABLE IF EXISTS wiki_fts")
            except sqlite3.OperationalError:
                pass
    raise RuntimeError(f"Could not create FTS5 table: {last_err}")


def _open() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    _ensure_table(conn)
    return conn


# --- ingest -----------------------------------------------------------------

def _parse_md(path: Path) -> tuple:
    """Reuse wiki_compiler's frontmatter parser so what we index matches what
    the UI surfaces. Returns (title, body, compiled_at)."""
    from services.wiki_compiler import _parse_frontmatter
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    return (
        meta.get("title") or path.stem,
        body.strip(),
        meta.get("compiled_at") or "",
    )


def _iter_dir(subdir: str, kind: str):
    base = WIKI_DIR / subdir
    if not base.exists():
        return
    for path in base.glob("*.md"):
        try:
            title, body, compiled_at = _parse_md(path)
        except OSError as e:
            _log(f"skip {path.name}: {e}")
            continue
        yield (
            kind,
            path.name,
            f"data/wiki/{subdir}/{path.name}",
            title,
            body,
            compiled_at,
        )


def rebuild_index() -> dict:
    """Wipe and rebuild the FTS index from disk. Cheap at this scale (~1s)."""
    conn = _open()
    try:
        with conn:
            conn.execute("DELETE FROM wiki_fts")
            counts = {"paper": 0, "concept": 0}
            for row in _iter_dir("papers", "paper"):
                conn.execute(
                    "INSERT INTO wiki_fts (kind, filename, path, title, body, compiled_at)"
                    " VALUES (?,?,?,?,?,?)",
                    row,
                )
                counts["paper"] += 1
            for row in _iter_dir("concepts", "concept"):
                conn.execute(
                    "INSERT INTO wiki_fts (kind, filename, path, title, body, compiled_at)"
                    " VALUES (?,?,?,?,?,?)",
                    row,
                )
                counts["concept"] += 1
        total = counts["paper"] + counts["concept"]
        _log(f"rebuilt: {total} docs ({counts['paper']} papers, {counts['concept']} concepts)")
        return {"indexed": total, **counts}
    finally:
        conn.close()


# --- query ------------------------------------------------------------------

# Strip everything except letters, digits, CJK chars, and hiragana/katakana.
# Anything else becomes a separator. This protects FTS5 from special chars
# (- + : ( ) * ^ ") without losing common alphanumeric or CJK input.
_TERM_SPLIT = re.compile(r"[^\w一-鿿぀-ヿ]+", re.UNICODE)


def _sanitize(query: str) -> str:
    """Wrap each term in double quotes so FTS5 treats it as a literal phrase
    (AND across terms by default). Empty input → empty string → caller skips
    the query."""
    parts = [t for t in _TERM_SPLIT.split(query) if t.strip()]
    if not parts:
        return ""
    quoted = [f'"{p}"' for p in parts]
    return " ".join(quoted)


def search(query: str, limit: int = 20) -> List[dict]:
    safe = _sanitize(query)
    if not safe:
        return []
    conn = _open()
    try:
        # bm25() is negative; lower (more negative) = better match.
        sql = """
        SELECT
            kind, filename, path, title, compiled_at,
            snippet(wiki_fts, 4, '<mark>', '</mark>', '…', 24) AS snippet,
            bm25(wiki_fts) AS score
        FROM wiki_fts
        WHERE wiki_fts MATCH ?
        ORDER BY score
        LIMIT ?
        """
        try:
            cur = conn.execute(sql, (safe, limit))
        except sqlite3.OperationalError as e:
            _log(f"query failed for {safe!r}: {e}")
            return []
        cols = ["kind", "filename", "path", "title", "compiled_at", "snippet", "score"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def index_stats() -> dict:
    """For debug / diagnostics endpoint. Returns total + per-kind counts."""
    conn = _open()
    try:
        totals = conn.execute("SELECT kind, COUNT(*) FROM wiki_fts GROUP BY kind").fetchall()
        return {
            "total": sum(c for _, c in totals),
            "by_kind": {k: c for k, c in totals},
            "db_path": str(DB_PATH),
        }
    finally:
        conn.close()
