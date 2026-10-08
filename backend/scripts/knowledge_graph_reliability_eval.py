#!/usr/bin/env python3
"""Offline evaluation for the 4A/5A/6A/3A knowledge-graph increment."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from models import Base, KnowledgeNode, Paper  # noqa: E402
from services.graph_analysis_service import analyze_graph  # noqa: E402
from services.graph_audit_service import diff_graphs, export_graph  # noqa: E402
from services.graph_mutation_service import (  # noqa: E402
    apply_mutation_plan,
    fragment_from_extraction,
    plan_summary,
    resolve_mutation_plan,
)
from services.pipeline_manifest import (  # noqa: E402
    extraction_is_reusable,
    record_extraction,
)


EXTRACTION = {
    "title": "Deterministic Graph Systems",
    "authors": ["Ada Example"],
    "abstract_summary": "A synthetic fixture for graph reliability evaluation.",
    "venue": "Fixture 2026",
    "year": 2026,
    "problem_area": "Knowledge Graphs",
    "keywords": ["provenance", "transactions"],
    "techniques": [
        {"name": "Mutation Planning", "aliases": ["MP"], "role": "writer", "builds_on": []},
        {"name": "Manifest Hashing", "aliases": [], "role": "invalidation", "builds_on": ["Mutation Planning"]},
    ],
    "datasets": [{"name": "SyntheticGraph", "purpose": "evaluation"}],
    "baselines": ["Direct ORM Write"],
}


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
    return ordered[index]


def run(samples: int) -> dict:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    plan_latencies: list[float] = []
    analysis_latencies: list[float] = []
    try:
        with sessions() as db, tempfile.TemporaryDirectory() as tmp:
            raw = json.dumps(EXTRACTION, ensure_ascii=False)
            paper = Paper(
                id="fixture-paper",
                filepath="papers/2601.01234v2.pdf",
                filename="2601.01234v2.pdf",
                file_hash="fixture-sha256",
                raw_llm_response=raw,
                extracted_text="doi:10.1234/FIXTURE.2026",
                processed=False,
            )
            db.add(paper)
            db.add(KnowledgeNode(
                id="legacy-paper-node",
                title="Legacy title",
                content="legacy",
                node_type="paper",
                source_paper_ids=["fixture-paper"],
                promotion_status="promoted",
            ))
            db.commit()

            cfg = {"extraction_prompt": "fixture-prompt"}
            manifest_root = Path(tmp) / "manifests"
            record_extraction(
                paper, cfg, "fixture-model", EXTRACTION, raw, root=manifest_root
            )
            manifest_reusable = extraction_is_reusable(
                paper, cfg, "fixture-model", root=manifest_root
            )

            fragment = fragment_from_extraction(
                EXTRACTION,
                paper_id=paper.id,
                source_sha256=paper.file_hash,
            )
            embeddings = {
                (node_type, title): [1.0, 0.0]
                for node_type, title in (
                    ("paper", "Deterministic Graph Systems"),
                    ("problem_area", "Knowledge Graphs"),
                    ("technique", "Mutation Planning"),
                    ("technique", "Manifest Hashing"),
                    ("dataset", "SyntheticGraph"),
                    ("technique", "Direct ORM Write"),
                )
            }
            hashes = []
            for _ in range(samples):
                started = time.perf_counter()
                plan = resolve_mutation_plan(
                    db,
                    fragment,
                    embeddings=embeddings,
                    similarity_threshold=0.99,
                )
                plan_latencies.append((time.perf_counter() - started) * 1000)
                hashes.append(plan_summary(plan)["plan_hash"])
            dry_before = (db.query(KnowledgeNode).count(),)
            preview = apply_mutation_plan(db, plan, dry_run=True)
            dry_after = (db.query(KnowledgeNode).count(),)
            before = export_graph(db)
            applied = apply_mutation_plan(db, plan)
            paper.processed = True
            db.commit()
            after = export_graph(db)
            diff = diff_graphs(before, after)

            second_plan = resolve_mutation_plan(
                db,
                fragment,
                embeddings=embeddings,
                similarity_threshold=0.99,
            )
            second_summary = plan_summary(second_plan)

            cache_path = Path(tmp) / "analysis.json"
            for _ in range(samples):
                started = time.perf_counter()
                analysis = analyze_graph(db, use_cache=False, cache_path=cache_path)
                analysis_latencies.append((time.perf_counter() - started) * 1000)
            cached = analyze_graph(db, cache_path=cache_path)

            checks = {
                "manifest_reusable": manifest_reusable,
                "plan_deterministic": len(set(hashes)) == 1,
                "dry_run_read_only": preview["dry_run"] and dry_before == dry_after,
                "mutation_applied": applied["applied"],
                "second_apply_has_no_new_nodes": second_summary["create_nodes"] == 0,
                "diff_detects_change": diff["changed"],
                "export_signatures_differ": before["signature"] != after["signature"],
                "analysis_cache_hit": cached["cache_hit"],
                "analysis_signature_stable": cached["graph_signature"] == analysis["graph_signature"],
                "no_model_calls": True,
            }
            return {
                "schema": "knowra.graph-reliability-eval.v1",
                "samples": samples,
                "checks": checks,
                "passed": sum(bool(value) for value in checks.values()),
                "total": len(checks),
                "metrics": {
                    "plan_ms": {
                        "mean": round(statistics.mean(plan_latencies), 3),
                        "p50": round(percentile(plan_latencies, 0.50), 3),
                        "p95": round(percentile(plan_latencies, 0.95), 3),
                        "max": round(max(plan_latencies), 3),
                    },
                    "analysis_ms": {
                        "mean": round(statistics.mean(analysis_latencies), 3),
                        "p50": round(percentile(analysis_latencies, 0.50), 3),
                        "p95": round(percentile(analysis_latencies, 0.95), 3),
                        "max": round(max(analysis_latencies), 3),
                    },
                    "diff_counts": diff["counts"],
                    "analysis_counts": analysis["counts"],
                    "model_calls": 0,
                },
            }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "knowledge_graph_reliability_eval.json",
    )
    args = parser.parse_args()
    report = run(max(1, args.samples))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
