#!/usr/bin/env python3
"""Measure bounded body-term fallback with a disposable synthetic Wiki."""
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

from services import wiki_search  # noqa: E402
from services.wiki_compiler import _render_frontmatter  # noqa: E402


QUESTION = "世界模型与动作模型的结合，在建模上经历了哪些变化？"
SINGLE_TERM_QUESTION = "世界模型在建模上经历了哪些变化？"
UNKNOWN_QUESTION = "完全未知且没有答案的研究问题？"


def write_page(path: Path, index: int, body: str) -> None:
    title = f"Synthetic Document {index:04d}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_frontmatter({
            "title": title,
            "aliases": [f"SD{index:04d}"],
            "slug": f"synthetic-{index:04d}",
            "compiled_at": "2026-10-30T00:00:00+00:00",
        })
        + f"\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


def summarize(result: dict) -> dict:
    probe = next(
        (attempt for attempt in result["attempts"] if attempt["mode"] == "body_probe"),
        None,
    )
    return {
        "mode": result["mode"],
        "terms": result["terms"],
        "attempt_modes": [attempt["mode"] for attempt in result["attempts"]],
        "hit_count": len(result["hits"]),
        "probe_count": probe["probe_count"] if probe else 0,
        "probe_limit": wiki_search.MAX_BODY_FALLBACK_PROBES,
        "common_rejected": probe["common_rejected"] if probe else 0,
    }


def timed_search(question: str, iterations: int) -> tuple[dict, list[float]]:
    samples: list[float] = []
    result: dict = {}
    for _ in range(iterations):
        started = perf_counter()
        result = wiki_search.search_with_fallback(question, limit=12)
        samples.append((perf_counter() - started) * 1000)
    return result, samples


def timed_strict(question: str, iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = perf_counter()
        wiki_search.search(question, limit=12)
        samples.append((perf_counter() - started) * 1000)
    return samples


def timed_pre_body_fallback(question: str, iterations: int) -> list[float]:
    """Measure the merged strict + title/alias miss path before this change."""
    samples: list[float] = []
    for _ in range(iterations):
        started = perf_counter()
        wiki_search.search(question, limit=12)
        wiki_search._extract_known_terms(question)
        samples.append((perf_counter() - started) * 1000)
    return samples


def timed_body_probe(question: str, iterations: int) -> list[float]:
    samples: list[float] = []
    for _ in range(iterations):
        started = perf_counter()
        wiki_search._extract_body_terms(question)
        samples.append((perf_counter() - started) * 1000)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, default=1075)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.documents < 120:
        raise SystemExit("Use at least 120 documents so common-term filtering is meaningful")

    with tempfile.TemporaryDirectory(prefix="knowra-body-fallback-measure-") as temporary:
        root = Path(temporary)
        wiki_dir = root / "wiki"
        db_path = root / "wiki_search.sqlite"
        for index in range(1, args.documents + 1):
            if index <= 6:
                body = "合成研究联合世界模型和动作模型进行闭环预测控制。"
            elif index <= 104:
                body = "合成研究使用世界模型预测环境状态。"
            elif index <= 119:
                body = "合成研究使用动作模型表达可控策略。"
            else:
                body = "这是一份不包含目标术语的合成背景材料。"
            subdir = "papers" if index <= 198 else "concepts"
            write_page(
                wiki_dir / subdir / f"{index:04d}-synthetic-{index:04d}.md",
                index,
                body,
            )

        with patch.object(wiki_search, "DB_PATH", db_path), patch.object(
            wiki_search, "WIKI_DIR", wiki_dir,
        ):
            rebuild = wiki_search.rebuild_index()
            strict_counts = {
                "two_term_question": len(wiki_search.search(QUESTION, limit=12)),
                "single_term_question": len(wiki_search.search(SINGLE_TERM_QUESTION, limit=12)),
            }

            wiki_search._clear_entity_cache()
            started = perf_counter()
            cold_result = wiki_search.search_with_fallback(QUESTION, limit=12)
            cold_ms = (perf_counter() - started) * 1000
            two_result, two_samples = timed_search(QUESTION, args.iterations)
            single_result, single_samples = timed_search(
                SINGLE_TERM_QUESTION,
                args.iterations,
            )
            unknown_result, unknown_samples = timed_search(
                UNKNOWN_QUESTION,
                args.iterations,
            )
            strict_result, strict_samples = timed_search(
                "Synthetic Document 0001",
                args.iterations,
            )
            strict_two_samples = timed_strict(QUESTION, args.iterations)
            strict_single_samples = timed_strict(
                SINGLE_TERM_QUESTION,
                args.iterations,
            )
            strict_unknown_samples = timed_strict(
                UNKNOWN_QUESTION,
                args.iterations,
            )
            pre_body_two_samples = timed_pre_body_fallback(QUESTION, args.iterations)
            pre_body_single_samples = timed_pre_body_fallback(
                SINGLE_TERM_QUESTION,
                args.iterations,
            )
            pre_body_unknown_samples = timed_pre_body_fallback(
                UNKNOWN_QUESTION,
                args.iterations,
            )
            body_probe_two_samples = timed_body_probe(QUESTION, args.iterations)
            body_probe_single_samples = timed_body_probe(
                SINGLE_TERM_QUESTION,
                args.iterations,
            )
            body_probe_unknown_samples = timed_body_probe(
                UNKNOWN_QUESTION,
                args.iterations,
            )

        two_p50 = statistics.median(two_samples)
        single_p50 = statistics.median(single_samples)
        unknown_p50 = statistics.median(unknown_samples)
        strict_two_p50 = statistics.median(strict_two_samples)
        strict_single_p50 = statistics.median(strict_single_samples)
        strict_unknown_p50 = statistics.median(strict_unknown_samples)
        pre_body_two_p50 = statistics.median(pre_body_two_samples)
        pre_body_single_p50 = statistics.median(pre_body_single_samples)
        pre_body_unknown_p50 = statistics.median(pre_body_unknown_samples)

        result = {
            "fixture": "disposable synthetic Markdown and SQLite only",
            "documents": args.documents,
            "iterations": args.iterations,
            "rebuild_ms": rebuild["duration_ms"],
            "strict_full_question_hits": strict_counts,
            "cold_two_term_ms": round(cold_ms, 3),
            "two_term": {
                **summarize(two_result),
                "strict_only_p50_ms": round(strict_two_p50, 3),
                "pre_body_fallback_p50_ms": round(pre_body_two_p50, 3),
                "body_probe_only_p50_ms": round(
                    statistics.median(body_probe_two_samples), 3,
                ),
                "warm_p50_ms": round(two_p50, 3),
                "warm_max_ms": round(max(two_samples), 3),
            },
            "single_term": {
                **summarize(single_result),
                "strict_only_p50_ms": round(strict_single_p50, 3),
                "pre_body_fallback_p50_ms": round(pre_body_single_p50, 3),
                "body_probe_only_p50_ms": round(
                    statistics.median(body_probe_single_samples), 3,
                ),
                "warm_p50_ms": round(single_p50, 3),
                "warm_max_ms": round(max(single_samples), 3),
            },
            "unknown": {
                **summarize(unknown_result),
                "strict_only_p50_ms": round(strict_unknown_p50, 3),
                "pre_body_fallback_p50_ms": round(pre_body_unknown_p50, 3),
                "body_probe_only_p50_ms": round(
                    statistics.median(body_probe_unknown_samples), 3,
                ),
                "warm_p50_ms": round(unknown_p50, 3),
                "warm_max_ms": round(max(unknown_samples), 3),
            },
            "strict_title": {
                **summarize(strict_result),
                "warm_p50_ms": round(statistics.median(strict_samples), 3),
                "warm_max_ms": round(max(strict_samples), 3),
            },
        }

    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
