#!/usr/bin/env python3
"""Measure Knowra's simple Wiki indexes with disposable synthetic corpora."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services import wiki_index, wiki_search  # noqa: E402
from services.wiki_compiler import _render_frontmatter  # noqa: E402
from services.wiki_metrics import estimate_tokens, wiki_directory_metrics  # noqa: E402


def write_page(path: Path, index: int, kind: str) -> dict:
    title = f"Synthetic {kind.title()} Topic {index:04d}"
    body = (
        f"Synthetic topic {index:04d} studies bounded local retrieval and evidence. "
        * 12
        + "合成材料只用于测量目录、索引和查询延迟。" * 18
    )
    meta = {
        "kind": kind,
        "title": title,
        "compiled_at": "2026-10-30T00:00:00+00:00",
        "slug": path.stem.split("-", 1)[-1],
    }
    if kind == "paper":
        meta["paper_id"] = index
    else:
        meta["concept_id"] = index
        meta["source_paper_ids"] = [max(1, index - 1)]
        meta["node_type"] = "concept"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_frontmatter(meta) + f"\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    return {
        **meta,
        "filename": path.name,
        "disk_path": str(path),
        "source_signature": f"synthetic-{kind}-{index}",
    }


def measure_size(document_count: int) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"knowra-wiki-measure-{document_count}-") as temporary:
        root = Path(temporary)
        wiki_dir = root / "wiki"
        db_path = root / "wiki_search.sqlite"
        index_path = wiki_dir / "index.md"
        paper_count = round(document_count * 0.8)
        papers = [
            write_page(
                wiki_dir / "papers" / f"{index:04d}-paper-{index:04d}.md",
                index,
                "paper",
            )
            for index in range(1, paper_count + 1)
        ]
        concepts = [
            write_page(
                wiki_dir / "concepts" / f"{index:04d}-concept-{index:04d}.md",
                index,
                "concept",
            )
            for index in range(paper_count + 1, document_count + 1)
        ]

        with patch.object(wiki_search, "DB_PATH", db_path), patch.object(
            wiki_search, "WIKI_DIR", wiki_dir,
        ):
            search_rebuild = wiki_search.rebuild_index()
            search_status = wiki_search.index_stats()
            search_samples = []
            for _ in range(25):
                started = perf_counter()
                hits = wiki_search.search("bounded local retrieval", limit=12)
                search_samples.append((perf_counter() - started) * 1000)
            assert hits

        with patch.object(wiki_index, "WIKI_DIR", wiki_dir), patch.object(
            wiki_index, "INDEX_PATH", index_path,
        ), patch.object(wiki_index, "list_paper_pages", return_value=papers), patch.object(
            wiki_index, "list_concept_pages", return_value=concepts,
        ):
            started = perf_counter()
            wiki_index.refresh_index()
            overview_refresh_ms = (perf_counter() - started) * 1000
            overview_status = wiki_index.index_summary()
            payload = wiki_index._gather_inputs(None)
            rebuild_prompt = wiki_index._user_prompt(payload)

        directory = wiki_directory_metrics(wiki_dir)
        return {
            "documents": document_count,
            "directory": directory,
            "overview": {
                "bytes": overview_status["size"],
                "estimated_tokens": overview_status["estimated_tokens"],
                "refresh_ms": round(overview_refresh_ms, 3),
                "status_ms": overview_status["read_latency_ms"],
                "full_rebuild_prompt_estimated_tokens": estimate_tokens(rebuild_prompt),
            },
            "full_text": {
                "db_bytes": search_rebuild["db_size"],
                "rebuild_ms": search_rebuild["duration_ms"],
                "status_ms": search_status["status_latency_ms"],
                "estimated_tokens": search_status["estimated_tokens"],
                "search_p50_ms": round(statistics.median(search_samples), 3),
                "search_max_ms": round(max(search_samples), 3),
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=[100, 500])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {
        "fixture": "synthetic Markdown only; no user Wiki/database/provider",
        "token_estimate": "cjk_chars + non_cjk_chars / 3.6; not a provider tokenizer",
        "measurements": [measure_size(size) for size in args.sizes],
    }
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
