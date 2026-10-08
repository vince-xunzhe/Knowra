"""Create a privacy-safe Phase-0 knowledge-graph baseline artifact."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from services.knowledge_graph_baseline import (  # noqa: E402
    BASELINE_SCHEMA_VERSION,
    GRAPH_RESPONSE_CONTRACT,
    benchmark_fts_rebuild,
    collect_cloud_migrations,
    collect_graph_metrics,
    collect_job_latency,
    compare_baselines,
    fixture_sha256,
    write_json,
)


def _git_value(*args: str) -> str | None:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    value = result.stdout.strip()
    return value or None


def _worktree_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def build_report(args: argparse.Namespace) -> dict:
    fixture = args.fixture.resolve()
    metrics = collect_graph_metrics(args.database)
    report = {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": _git_value("rev-parse", "--short", "HEAD"),
        "source_branch": _git_value("branch", "--show-current"),
        "source_worktree_dirty": _worktree_dirty(),
        "privacy": {
            "aggregate_only": True,
            "excluded": [
                "paper titles and paths",
                "node ids, titles, contents and embeddings",
                "wiki contents",
                "raw LLM prompts and responses",
                "credentials",
            ],
        },
        **metrics,
        "background_job_latency": collect_job_latency(args.tasks_database),
        "fts_rebuild": benchmark_fts_rebuild(args.wiki_dir, args.fts_trials)
        if args.benchmark_fts
        else {"available": False, "reason": "benchmark not requested"},
        "api_contract": GRAPH_RESPONSE_CONTRACT,
        "evaluation_fixture": {
            "path": str(fixture.relative_to(ROOT)),
            "sha256": fixture_sha256(fixture),
        },
        "cloud_schema": {
            "migrations": collect_cloud_migrations(args.migrations_dir),
            "knowledge_graph_migration": "0003_knowledge.sql",
        },
    }
    if args.compare_to:
        reference = json.loads(args.compare_to.read_text(encoding="utf-8"))
        report["comparison"] = compare_baselines(reference, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "knowledge.db")
    parser.add_argument(
        "--tasks-database", type=Path, default=ROOT / "data" / "tasks.db"
    )
    parser.add_argument("--wiki-dir", type=Path, default=ROOT / "data" / "wiki")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "backend" / "tests" / "fixtures" / "knowledge_graph_phase0.json",
    )
    parser.add_argument(
        "--migrations-dir", type=Path, default=ROOT / "supabase" / "migrations"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "knowledge_graph_phase0_baseline.json",
    )
    parser.add_argument("--benchmark-fts", action="store_true")
    parser.add_argument("--fts-trials", type=int, default=3)
    parser.add_argument("--compare-to", type=Path)
    args = parser.parse_args()

    if not args.database.is_file():
        parser.error(f"knowledge database not found: {args.database}")
    if not args.fixture.is_file():
        parser.error(f"evaluation fixture not found: {args.fixture}")

    report = build_report(args)
    write_json(args.output, report)
    print(
        f"Phase-0 baseline written to {args.output}: "
        f"{report['graph']['all']['nodes']} nodes, "
        f"{report['graph']['all']['edges']} edges."
    )


if __name__ == "__main__":
    main()
