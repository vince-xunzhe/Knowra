"""Offline Phase-2 graph-query and Hybrid Ask evidence evaluation."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import Base, KnowledgeEdge, KnowledgeNode
from services import graph_query_service as query
from services.ask_agent import TOOLS


def _ids(result):
    return [node["id"] for node in result.get("nodes", [])]


def evaluate(fixture_path: Path, iterations: int = 25) -> dict:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add_all([KnowledgeNode(
            id=node["id"], title=node["title"], content=node["title"],
            node_type=node["node_type"], tags=node.get("tags", []),
            hidden=node["hidden"], promotion_status=node["promotion_status"],
        ) for node in fixture["nodes"]])
        db.add_all([KnowledgeEdge(
            id=edge["id"], source_id=edge["source"], target_id=edge["target"],
            relation_type=edge["relation_type"], weight=edge["weight"], origin="legacy",
        ) for edge in fixture["edges"]])
        db.commit()

        def run(case):
            op, args = case["operation"], case["input"]
            if op == "neighbors":
                return _ids(query.get_neighbors(db, args["node_id"]))
            if op == "shortest_path":
                return _ids(query.shortest_path(db, args["source_id"], args["target_id"]))
            if op == "shared_concepts":
                return _ids(query.find_shared_neighbors(db, args["paper_ids"]))
            if op == "papers_for_concept":
                return _ids(query.find_papers_for_concept(db, args["concept_id"]))
            if op == "visible_search":
                return _ids(query.find_nodes(db, args["query"]))
            raise ValueError(op)

        exact = []
        timings = []
        for case in fixture["structure_cases"]:
            actual = run(case)
            exact.append({"id": case["id"], "passed": actual == case["expected_node_ids"]})
            for _ in range(iterations):
                started = perf_counter()
                run(case)
                timings.append((perf_counter() - started) * 1000)

    engine.dispose()
    available_tools = {item["function"]["name"] for item in TOOLS}
    wiki_files = {item["filename"] for item in fixture["wiki_documents"]}
    ask_checks = []
    for case in fixture["ask_cases"]:
        tools_ready = set(case.get("expected_tools", [])).issubset(available_tools)
        evidence_ready = set(case.get("required_evidence", [])).issubset(wiki_files)
        ask_checks.append({
            "id": case["id"],
            "category": case["category"],
            "passed": tools_ready and evidence_ready,
            "expected_tool_calls": len(case.get("expected_tools", [])),
        })
    ordered = sorted(timings)
    percentile = lambda p: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]
    return {
        "schema_version": 1,
        "mode": "offline_deterministic",
        "structure_quality": {
            "passed": sum(item["passed"] for item in exact),
            "total": len(exact),
            "cases": exact,
        },
        "ask_evidence_readiness": {
            "passed": sum(item["passed"] for item in ask_checks),
            "total": len(ask_checks),
            "cases": ask_checks,
        },
        "latency_ms": {
            "samples": len(timings),
            "mean": round(statistics.mean(timings), 3),
            "p50": round(percentile(0.50), 3),
            "p95": round(percentile(0.95), 3),
            "max": round(max(timings), 3),
        },
        "cost": {
            "graph_query_model_tokens": 0,
            "expected_tool_calls_total": sum(item["expected_tool_calls"] for item in ask_checks),
            "expected_tool_calls_mean": round(statistics.mean(item["expected_tool_calls"] for item in ask_checks), 3),
        },
        "note": "Offline evidence/tool contract evaluation; language-model answer wording is covered by mocked tool-loop integration tests, not billed live calls.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=Path("backend/tests/fixtures/knowledge_graph_phase0.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/knowledge_graph_phase2_eval.json"))
    parser.add_argument("--iterations", type=int, default=25)
    args = parser.parse_args()
    report = evaluate(args.fixture, max(1, args.iterations))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
