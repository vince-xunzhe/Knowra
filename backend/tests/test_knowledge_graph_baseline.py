from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, KnowledgeEdge, KnowledgeNode, Paper
from services.graph_service import get_graph_data
from services.knowledge_graph_baseline import (
    GRAPH_RESPONSE_CONTRACT,
    collect_graph_metrics,
    compare_baselines,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "knowledge_graph_phase0.json"


def _visible_nodes(payload):
    return {
        node["id"]: node
        for node in payload["nodes"]
        if not node["hidden"]
        and (
            node["node_type"] == "paper"
            or node["node_type"] not in {"technique", "dataset", "problem_area", "concept"}
            or node["promotion_status"] == "promoted"
        )
    }


def _adjacency(payload):
    visible = _visible_nodes(payload)
    adjacency = {node_id: set() for node_id in visible}
    for edge in payload["edges"]:
        source, target = edge["source"], edge["target"]
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
            adjacency[target].add(source)
    return visible, adjacency


def _run_reference_case(payload, case):
    visible, adjacency = _adjacency(payload)
    operation = case["operation"]
    inputs = case["input"]
    if operation == "neighbors":
        return sorted(adjacency[inputs["node_id"]])
    if operation == "shortest_path":
        source, target = inputs["source_id"], inputs["target_id"]
        queue = deque([(source, [source])])
        seen = {source}
        while queue:
            current, path = queue.popleft()
            if current == target:
                return path
            for neighbor in sorted(adjacency.get(current, ())):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, [*path, neighbor]))
        return []
    if operation == "shared_concepts":
        paper_ids = inputs["paper_ids"]
        shared = set(adjacency[paper_ids[0]])
        for paper_id in paper_ids[1:]:
            shared &= adjacency[paper_id]
        return sorted(node_id for node_id in shared if visible[node_id]["node_type"] != "paper")
    if operation == "papers_for_concept":
        return sorted(
            node_id
            for node_id in adjacency[inputs["concept_id"]]
            if visible[node_id]["node_type"] == "paper"
        )
    if operation == "visible_search":
        query = inputs["query"].lower()
        return sorted(
            node_id
            for node_id, node in visible.items()
            if query in node["title"].lower()
            or any(query == str(tag).lower() for tag in node.get("tags", []))
        )
    raise AssertionError(f"unknown operation: {operation}")


class KnowledgeGraphPhase0Tests(unittest.TestCase):
    def test_synthetic_structure_and_ask_cases_are_complete_and_consistent(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        categories = {case["category"] for case in payload["ask_cases"]}
        self.assertEqual(categories, {"content", "structure", "hybrid"})
        self.assertGreaterEqual(len(payload["ask_cases"]), 6)
        required_operations = {
            "neighbors",
            "shortest_path",
            "shared_concepts",
            "papers_for_concept",
            "visible_search",
        }
        self.assertTrue(
            required_operations.issubset(
                {case["operation"] for case in payload["structure_cases"]}
            )
        )
        for case in payload["structure_cases"]:
            self.assertEqual(_run_reference_case(payload, case), case["expected_node_ids"])

    def test_graph_response_contract_is_frozen_against_synthetic_database(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        with sessions() as db:
            paper = Paper(
                id="paper-1",
                filepath="paper.pdf",
                filename="paper.pdf",
                file_hash="hash-1",
                title="Synthetic Paper",
                processed=True,
            )
            paper_node = KnowledgeNode(
                id="node-paper",
                title="Synthetic Paper",
                content="Synthetic",
                node_type="paper",
                promotion_status="promoted",
                source_paper_ids=["paper-1"],
            )
            concept = KnowledgeNode(
                id="node-concept",
                title="Synthetic Concept",
                content="Synthetic",
                node_type="concept",
                promotion_status="promoted",
                source_paper_ids=["paper-1"],
            )
            db.add_all([paper, paper_node, concept])
            db.flush()
            db.add(
                KnowledgeEdge(
                    id="edge-1",
                    source_id=paper_node.id,
                    target_id=concept.id,
                    relation_type="uses",
                    weight=1.0,
                )
            )
            db.commit()
            result = get_graph_data(db)

        self.assertEqual(set(result), set(GRAPH_RESPONSE_CONTRACT["top_level_required"]))
        self.assertEqual(set(result["nodes"][0]), set(GRAPH_RESPONSE_CONTRACT["node_required"]))
        self.assertTrue(
            set(GRAPH_RESPONSE_CONTRACT["edge_required"]).issubset(result["edges"][0])
        )
        engine.dispose()

    def test_collector_is_aggregate_only_and_comparison_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE knowledge_nodes (
                  id TEXT PRIMARY KEY, title TEXT, content TEXT, node_type TEXT,
                  hidden BOOLEAN, promotion_status TEXT
                );
                CREATE TABLE knowledge_edges (
                  id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT,
                  relation_type TEXT, weight REAL
                );
                CREATE TABLE llm_calls (
                  id TEXT PRIMARY KEY, task TEXT, success BOOLEAN, latency_ms INTEGER
                );
                CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT, set_at TEXT);
                INSERT INTO knowledge_nodes VALUES
                  ('p1','Private Paper','private body','paper',0,'promoted'),
                  ('c1','Private Concept','private body','concept',0,'promoted'),
                  ('c2','Hidden Concept','private body','concept',1,'rejected');
                INSERT INTO knowledge_edges VALUES
                  ('e1','p1','c1','uses',1.0),
                  ('e2','p1','missing','related',0.1);
                INSERT INTO llm_calls VALUES
                  ('l1','paper_extract',1,100),
                  ('l2','paper_extract',1,300),
                  ('l3','ask_agent',0,50);
                INSERT INTO _meta VALUES ('multitenant_v1','private','2026-01-01');
                """
            )
            conn.close()
            metrics = collect_graph_metrics(path)

        encoded = json.dumps(metrics)
        self.assertNotIn("Private Paper", encoded)
        self.assertNotIn("Private Concept", encoded)
        self.assertNotIn("private body", encoded)
        self.assertNotIn("missing", encoded)
        self.assertEqual(metrics["graph"]["all"]["nodes"], 3)
        self.assertEqual(metrics["graph"]["broken_edge_references"], 1)
        self.assertEqual(
            metrics["model_call_latency"]["paper_extract"]["successful_latency"]["p50_ms"],
            200.0,
        )

        baseline = {**metrics, "api_contract": GRAPH_RESPONSE_CONTRACT}
        comparison = compare_baselines(baseline, baseline)
        self.assertFalse(comparison["api_contract_changed"])
        self.assertTrue(
            all(item["delta"] == 0 for item in comparison["metric_deltas"].values())
        )


if __name__ == "__main__":
    unittest.main()
