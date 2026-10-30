"""Read-only size/token/latency metrics for the compiled local Wiki.

Token counts are deliberately labelled estimates: they use the same cheap
CJK-aware heuristic as the prompt editor instead of depending on a provider-
specific tokenizer.  The helpers accept explicit paths so tests and benchmark
scripts can stay entirely inside disposable fixtures.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from time import perf_counter


TOKEN_ESTIMATE_METHOD = "cjk_chars + non_cjk_chars / 3.6"
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f]")


def estimate_tokens(text: str) -> int:
    cjk = 0
    other = 0
    for char in text or "":
        if _CJK_RE.match(char):
            cjk += 1
        else:
            other += 1
    return round(cjk + other / 3.6)


def wiki_directory_metrics(wiki_dir: Path) -> dict:
    """Measure compiled paper/concept Markdown without changing any index."""
    started = perf_counter()
    total_bytes = 0
    total_chars = 0
    estimated_tokens = 0
    unreadable_files = 0
    latest_modified_ns = 0
    by_kind = {"paper": 0, "concept": 0}

    for subdir, kind in (("papers", "paper"), ("concepts", "concept")):
        base = wiki_dir / subdir
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if not path.is_file():
                continue
            by_kind[kind] += 1
            try:
                stat = path.stat()
                total_bytes += stat.st_size
                latest_modified_ns = max(latest_modified_ns, stat.st_mtime_ns)
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                unreadable_files += 1
                continue
            total_chars += len(text)
            estimated_tokens += estimate_tokens(text)

    return {
        "files": by_kind["paper"] + by_kind["concept"],
        "by_kind": by_kind,
        "bytes": total_bytes,
        "characters": total_chars,
        "estimated_tokens": estimated_tokens,
        "token_estimate_method": TOKEN_ESTIMATE_METHOD,
        "unreadable_files": unreadable_files,
        "latest_modified_ns": latest_modified_ns,
        "measurement_ms": int((perf_counter() - started) * 1000),
    }


def atomic_write_text(path: Path, text: str) -> None:
    """Replace a text file only after the complete new version is durable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
