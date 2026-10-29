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
from typing import Any, List, Optional

from path_utils import DATA_DIR


DB_PATH = DATA_DIR / "wiki_search.sqlite"
WIKI_DIR = DATA_DIR / "wiki"

MAX_FALLBACK_TERMS = 6
MAX_FRONTMATTER_CHARS = 64_000
_EntitySignature = tuple[str, int, int, str]
_ENTITY_CACHE: Optional[
    tuple[_EntitySignature, tuple[tuple[str, str], ...]]
] = None


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


def _clear_entity_cache() -> None:
    global _ENTITY_CACHE
    _ENTITY_CACHE = None


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
        _clear_entity_cache()
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


def _search_match(match_query: str, limit: int) -> List[dict]:
    if not match_query:
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
            cur = conn.execute(sql, (match_query, limit))
        except sqlite3.OperationalError as e:
            _log(f"query failed for {match_query!r}: {e}")
            return []
        cols = ["kind", "filename", "path", "title", "compiled_at", "snippet", "score"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def search(query: str, limit: int = 20) -> List[dict]:
    safe = _sanitize(query)
    return _search_match(safe, limit)


def _entity_cache_signature() -> _EntitySignature:
    try:
        stat = DB_PATH.stat()
        mtime_ns, size = stat.st_mtime_ns, stat.st_size
    except OSError:
        mtime_ns, size = 0, 0
    return (str(DB_PATH.resolve()), mtime_ns, size, str(WIKI_DIR.resolve()))


def _known_entity_surfaces() -> list[tuple[str, str]]:
    """Return (surface, canonical title) pairs from the compiled Wiki.

    The FTS table remains unchanged. Aliases and slugs are read from page
    frontmatter only when a strict Ask query misses, then cached until the
    derived FTS database changes.
    """
    global _ENTITY_CACHE
    signature = _entity_cache_signature()
    cached = _ENTITY_CACHE
    if cached is not None and cached[0] == signature:
        return list(cached[1])

    from services.wiki_compiler import _parse_frontmatter

    conn = _open()
    try:
        rows = conn.execute(
            "SELECT kind, filename, title FROM wiki_fts ORDER BY kind, filename"
        ).fetchall()
    finally:
        conn.close()

    surfaces: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, filename, raw_title in rows:
        title = str(raw_title or Path(filename).stem).strip()
        if not title:
            continue
        candidates: list[Any] = [title]
        subdir = "papers" if kind == "paper" else "concepts"
        path = WIKI_DIR / subdir / filename
        try:
            frontmatter_lines: list[str] = []
            frontmatter_size = 0
            with path.open("r", encoding="utf-8") as stream:
                if stream.readline() != "---\n":
                    meta = {}
                else:
                    for line in stream:
                        frontmatter_size += len(line)
                        if frontmatter_size > MAX_FRONTMATTER_CHARS:
                            meta = {}
                            break
                        if line == "---\n":
                            text = "---\n" + "".join(frontmatter_lines) + "---\n"
                            meta, _ = _parse_frontmatter(text)
                            break
                        frontmatter_lines.append(line)
                    else:
                        meta = {}
        except (OSError, UnicodeError):
            meta = {}
        aliases = meta.get("aliases")
        if isinstance(aliases, list):
            candidates.extend(aliases)
        candidates.append(meta.get("slug"))
        for raw_surface in candidates:
            surface = str(raw_surface or "").strip()
            normalized = surface.casefold()
            key = (normalized, title.casefold())
            if len(normalized) < 2 or key in seen:
                continue
            seen.add(key)
            surfaces.append((surface, title))

    # One immutable snapshot avoids publishing a key from one rebuild with
    # entity values from another when concurrent Ask requests race a reindex.
    _ENTITY_CACHE = (signature, tuple(surfaces))
    return surfaces


def _surface_spans(query: str, surface: str) -> list[tuple[int, int]]:
    if surface.isascii():
        escaped = re.escape(surface)
        prefix = r"(?<![A-Za-z0-9_])" if surface[0].isalnum() else ""
        suffix = r"(?![A-Za-z0-9_])" if surface[-1].isalnum() else ""
        return [
            match.span()
            for match in re.finditer(prefix + escaped + suffix, query, re.IGNORECASE)
        ]
    folded_query = query.casefold()
    folded_surface = surface.casefold()
    spans: list[tuple[int, int]] = []
    start = folded_query.find(folded_surface)
    while start >= 0:
        spans.append((start, start + len(folded_surface)))
        start = folded_query.find(folded_surface, start + 1)
    return spans


def _extract_known_terms(query: str) -> list[str]:
    """Resolve explicit titles/aliases in a question to canonical titles.

    Longest non-overlapping mentions win, so a generic concept such as
    ``模型`` does not crowd out two explicit mentions such as ``世界模型`` and
    ``动作模型``. This is deterministic entity matching, not synonym search.
    """
    matches: list[tuple[int, int, str, str]] = []
    for surface, canonical in _known_entity_surfaces():
        for start, end in _surface_spans(query, surface):
            matches.append((start, end, surface, canonical))

    selected: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for start, end, _surface, canonical in sorted(
        matches,
        key=lambda item: (-(item[1] - item[0]), item[0], item[3].casefold()),
    ):
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        occupied.append((start, end))
        selected.append((start, end, canonical))

    terms: list[str] = []
    seen: set[str] = set()
    for _start, _end, canonical in sorted(selected, key=lambda item: item[0]):
        normalized = canonical.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        terms.append(canonical)
        if len(terms) >= MAX_FALLBACK_TERMS:
            break
    return terms


def _combine_terms(terms: list[str], operator: str) -> str:
    groups = [_sanitize(term) for term in terms]
    groups = [group for group in groups if group]
    if not groups:
        return ""
    if len(groups) == 1:
        return groups[0]
    return f" {operator} ".join(f"({group})" for group in groups)


def search_with_fallback(query: str, limit: int = 20) -> dict[str, Any]:
    """Search for Ask with a strict-first deterministic entity fallback.

    Existing strict hits and their ordering are returned unchanged. Only a
    zero-hit query consults known Wiki titles/aliases. AND matches are ranked
    first; OR matches fill the remaining slots without duplicates.
    """
    strict_query = _sanitize(query)
    strict_hits = _search_match(strict_query, limit)
    attempts = [{
        "mode": "strict",
        "query": strict_query,
        "hit_count": len(strict_hits),
    }]
    if strict_hits:
        return {
            "mode": "strict",
            "query": query,
            "terms": [],
            "attempts": attempts,
            "hits": strict_hits,
        }

    terms = _extract_known_terms(query)
    if not terms:
        return {
            "mode": "strict_no_match",
            "query": query,
            "terms": [],
            "attempts": attempts,
            "hits": [],
        }

    if len(terms) == 1:
        entity_query = _combine_terms(terms, "AND")
        entity_hits = _search_match(entity_query, limit)
        attempts.append({
            "mode": "entity",
            "query": entity_query,
            "hit_count": len(entity_hits),
        })
        return {
            "mode": "entity",
            "query": query,
            "terms": terms,
            "attempts": attempts,
            "hits": entity_hits,
        }

    and_query = _combine_terms(terms, "AND")
    and_hits = _search_match(and_query, limit)
    attempts.append({
        "mode": "entity_and",
        "query": and_query,
        "hit_count": len(and_hits),
    })

    or_query = _combine_terms(terms, "OR")
    or_hits = _search_match(or_query, limit)
    attempts.append({
        "mode": "entity_or",
        "query": or_query,
        "hit_count": len(or_hits),
    })

    merged: list[dict] = []
    seen_hits: set[tuple[Any, Any]] = set()
    for hit in [*and_hits, *or_hits]:
        key = (hit.get("kind"), hit.get("filename"))
        if key in seen_hits:
            continue
        seen_hits.add(key)
        merged.append(hit)
        if len(merged) >= limit:
            break
    return {
        "mode": "entity_and_or",
        "query": query,
        "terms": terms,
        "attempts": attempts,
        "hits": merged,
    }


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
