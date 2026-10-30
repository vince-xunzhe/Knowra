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
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from time import perf_counter
from typing import Any, List, Optional

from path_utils import DATA_DIR
from services.wiki_metrics import estimate_tokens, wiki_directory_metrics


DB_PATH = DATA_DIR / "wiki_search.sqlite"
WIKI_DIR = DATA_DIR / "wiki"

MAX_FALLBACK_TERMS = 6
MAX_FRONTMATTER_CHARS = 64_000
MAX_BODY_FALLBACK_TERMS = 4
MAX_BODY_FALLBACK_PROBES = 64
MIN_BODY_TERM_CHARS = 3
MAX_BODY_TERM_CHARS = 8
BODY_COMMON_MIN_DOCS = 8
BODY_COMMON_DOC_RATIO = 0.15
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


def _open(path: Optional[Path] = None) -> sqlite3.Connection:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
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
        except (OSError, UnicodeError) as e:
            _log(f"cannot index {path.name}: {e}")
            raise
        yield (
            kind,
            path.name,
            f"data/wiki/{subdir}/{path.name}",
            title,
            body,
            compiled_at,
        )


def rebuild_index() -> dict:
    """Build a complete shadow FTS database, then atomically replace the old one.

    Search requests continue to use the prior database while the shadow build
    runs.  Any parse, SQLite, or replace failure removes only the shadow file,
    so a previously healthy index remains available.
    """
    started = perf_counter()
    previous_exists = DB_PATH.is_file()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle, raw_temporary_path = tempfile.mkstemp(
        dir=DB_PATH.parent,
        prefix=f".{DB_PATH.name}.",
        suffix=".tmp",
    )
    os.close(handle)
    temporary_path: Optional[Path] = Path(raw_temporary_path)
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _open(temporary_path)
        with conn:
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
        conn.close()
        conn = None
        os.replace(temporary_path, DB_PATH)
        temporary_path = None
        total = counts["paper"] + counts["concept"]
        _clear_entity_cache()
        _log(f"rebuilt: {total} docs ({counts['paper']} papers, {counts['concept']} concepts)")
        return {
            "indexed": total,
            **counts,
            "db_size": DB_PATH.stat().st_size,
            "duration_ms": int((perf_counter() - started) * 1000),
            "replaced_previous": previous_exists,
        }
    finally:
        if conn is not None:
            conn.close()
        if temporary_path is not None:
            for candidate in (
                temporary_path,
                Path(str(temporary_path) + "-journal"),
                Path(str(temporary_path) + "-wal"),
                Path(str(temporary_path) + "-shm"),
            ):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass


# --- query ------------------------------------------------------------------

# Strip everything except letters, digits, CJK chars, and hiragana/katakana.
# Anything else becomes a separator. This protects FTS5 from special chars
# (- + : ( ) * ^ ") without losing common alphanumeric or CJK input.
_TERM_SPLIT = re.compile(r"[^\w一-鿿぀-ヿ]+", re.UNICODE)
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_BODY_CONNECTOR = re.compile(r"以及|并且|或者|还是|与|和|及|或")
_BODY_QUERY_FRAMING = {
    "哪些变化",
    "有什么变化",
    "发生了什么",
    "经历了哪些变化",
    "如何实现",
    "怎么实现",
    "为什么",
    "是什么",
    "有哪些",
}
_BODY_TECHNICAL_SUFFIXES = (
    "模型", "方法", "算法", "系统", "数据集", "任务", "框架", "架构",
    "策略", "表示", "表征", "网络", "基准", "指标", "机制", "模块",
    "代理", "智能体",
)


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


def _body_fragments(query: str) -> list[tuple[int, str]]:
    fragments: list[tuple[int, str]] = []
    for run_match in _CJK_RUN.finditer(query):
        run = run_match.group(0)
        cursor = 0
        for connector in _BODY_CONNECTOR.finditer(run):
            left_length = connector.start() - cursor
            right_length = len(run) - connector.end()
            if left_length >= MIN_BODY_TERM_CHARS and right_length >= MIN_BODY_TERM_CHARS:
                fragments.append((run_match.start() + cursor, run[cursor:connector.start()]))
                cursor = connector.end()
        if cursor < len(run):
            fragments.append((run_match.start() + cursor, run[cursor:]))
    return fragments


def _valid_body_candidate(phrase: str) -> bool:
    return not (
        phrase in _BODY_QUERY_FRAMING
        or phrase[0] in "的了在是有把被请问从对"
        or phrase[-1] in "的了吗呢么在上中"
    )


def _body_candidate_spans(query: str) -> list[tuple[int, int, str]]:
    """Generate bounded technical CJK phrases from a question.

    Only the longest valid window ending in a known technical suffix is kept.
    This deliberately avoids arbitrary n-grams and suffix-internal fragments,
    which can turn rare question wording into unrelated evidence.
    """
    fragments = _body_fragments(query)
    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for fragment_start, fragment in fragments:
        for suffix in _BODY_TECHNICAL_SUFFIXES:
            search_start = 0
            while True:
                suffix_start = fragment.find(suffix, search_start)
                if suffix_start < 0:
                    break
                phrase_end = suffix_start + len(suffix)
                for size in range(
                    min(MAX_BODY_TERM_CHARS, phrase_end),
                    MIN_BODY_TERM_CHARS - 1,
                    -1,
                ):
                    local_start = phrase_end - size
                    phrase = fragment[local_start:phrase_end]
                    if phrase in seen or not _valid_body_candidate(phrase):
                        continue
                    seen.add(phrase)
                    start = fragment_start + local_start
                    candidates.append((start, start + size, phrase))
                    if len(candidates) >= MAX_BODY_FALLBACK_PROBES:
                        return candidates
                    # Keep the longest valid phrase ending at this technical
                    # suffix. Shorter suffix windows (for example 界模型 from
                    # 世界模型) can match unrelated compounds such as 边界模型.
                    break
                search_start = suffix_start + 1

    return candidates


def _extract_body_terms(query: str) -> tuple[list[str], dict[str, Any]]:
    """Recover exact question phrases that occur in indexed titles or bodies.

    This path runs only after strict and title/alias recovery both fail. It uses
    at most ``MAX_BODY_FALLBACK_PROBES`` FTS counts on one connection, rejects
    phrases present in at least 15% of a non-trivial corpus, then keeps
    the longest non-overlapping technical-looking spans. It is lexical recall,
    not synonym or semantic retrieval.
    """
    candidates = _body_candidate_spans(query)
    diagnostics: dict[str, Any] = {
        "mode": "body_probe",
        "candidate_count": len(candidates),
        "probe_count": 0,
        "probe_limit": MAX_BODY_FALLBACK_PROBES,
        "matched_count": 0,
        "common_rejected": 0,
        "selected_count": 0,
    }
    if not candidates:
        return [], diagnostics

    conn = _open()
    matched: list[tuple[int, int, str, int]] = []
    try:
        total_docs = int(conn.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0])
        common_cutoff = max(
            BODY_COMMON_MIN_DOCS,
            ceil(total_docs * BODY_COMMON_DOC_RATIO),
        )
        for start, end, phrase in candidates:
            match_query = _sanitize(phrase)
            diagnostics["probe_count"] += 1
            try:
                count = int(conn.execute(
                    "SELECT COUNT(*) FROM wiki_fts WHERE wiki_fts MATCH ?",
                    (match_query,),
                ).fetchone()[0])
            except sqlite3.OperationalError as exc:
                _log(f"body fallback probe failed for {match_query!r}: {exc}")
                continue
            if count <= 0:
                continue
            diagnostics["matched_count"] += 1
            if count >= common_cutoff:
                diagnostics["common_rejected"] += 1
            else:
                matched.append((start, end, phrase, count))
    finally:
        conn.close()

    ranked = sorted(
        matched,
        key=lambda item: (
            -int(item[2].endswith(_BODY_TECHNICAL_SUFFIXES)),
            -(item[1] - item[0]),
            item[3],
            item[0],
        ),
    )
    selected: list[tuple[int, int, str]] = []
    for start, end, phrase, _count in ranked:
        if any(start < used_end and end > used_start for used_start, used_end, _ in selected):
            continue
        selected.append((start, end, phrase))
        if len(selected) >= MAX_BODY_FALLBACK_TERMS:
            break
    terms = [phrase for _start, _end, phrase in sorted(selected)]
    diagnostics["selected_count"] = len(terms)
    return terms, diagnostics


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
    zero-hit query consults known Wiki titles/aliases, then bounded exact
    phrases that are verified against the indexed corpus. AND matches are
    ranked first; OR matches fill the remaining slots without duplicates.
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
    source = "entity"
    if not terms:
        terms, body_diagnostics = _extract_body_terms(query)
        if body_diagnostics["probe_count"]:
            attempts.append(body_diagnostics)
        source = "body"
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
            "mode": source,
            "query": entity_query,
            "hit_count": len(entity_hits),
        })
        return {
            "mode": source,
            "query": query,
            "terms": terms,
            "attempts": attempts,
            "hits": entity_hits,
        }

    and_query = _combine_terms(terms, "AND")
    and_hits = _search_match(and_query, limit)
    attempts.append({
        "mode": f"{source}_and",
        "query": and_query,
        "hit_count": len(and_hits),
    })

    or_query = _combine_terms(terms, "OR")
    or_hits = _search_match(or_query, limit)
    attempts.append({
        "mode": f"{source}_or",
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
        "mode": f"{source}_and_or",
        "query": query,
        "terms": terms,
        "attempts": attempts,
        "hits": merged,
    }


def index_stats() -> dict:
    """Return read-only health and scale metrics without creating an index."""
    started = perf_counter()
    source = wiki_directory_metrics(WIKI_DIR)
    base = {
        "exists": DB_PATH.is_file(),
        "healthy": False,
        "total": 0,
        "by_kind": {},
        "db_path": str(DB_PATH),
        "db_size": 0,
        "indexed_characters": 0,
        "estimated_tokens": 0,
        "source": source,
        "stale": source["files"] > 0,
    }
    if not DB_PATH.is_file():
        base["status_latency_ms"] = int((perf_counter() - started) * 1000)
        return base

    try:
        stat = DB_PATH.stat()
        uri = f"{DB_PATH.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except (OSError, sqlite3.Error) as exc:
        base.update({
            "error": str(exc),
            "status_latency_ms": int((perf_counter() - started) * 1000),
        })
        return base
    try:
        table = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='wiki_fts'"
        ).fetchone()
        if not table:
            raise sqlite3.DatabaseError("wiki_fts table missing")
        totals = conn.execute(
            "SELECT kind, COUNT(*) FROM wiki_fts GROUP BY kind"
        ).fetchall()
        rows = conn.execute("SELECT title, body FROM wiki_fts").fetchall()
        by_kind = {str(kind): int(count) for kind, count in totals}
        total = sum(by_kind.values())
        indexed_characters = sum(len(title or "") + len(body or "") for title, body in rows)
        estimated = sum(estimate_tokens(title or "") + estimate_tokens(body or "") for title, body in rows)
        expected = source["by_kind"]
        return {
            **base,
            "exists": True,
            "healthy": True,
            "total": total,
            "by_kind": by_kind,
            "db_size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "indexed_characters": indexed_characters,
            "estimated_tokens": estimated,
            "token_estimate_method": source["token_estimate_method"],
            "stale": (
                by_kind.get("paper", 0) != expected.get("paper", 0)
                or by_kind.get("concept", 0) != expected.get("concept", 0)
                or source["unreadable_files"] > 0
                or source["latest_modified_ns"] > stat.st_mtime_ns
            ),
            "status_latency_ms": int((perf_counter() - started) * 1000),
        }
    except sqlite3.Error as exc:
        return {
            **base,
            "exists": True,
            "db_size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "error": str(exc),
            "status_latency_ms": int((perf_counter() - started) * 1000),
        }
    finally:
        conn.close()
