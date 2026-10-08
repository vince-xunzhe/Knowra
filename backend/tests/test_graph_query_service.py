from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import Base, KnowledgeEdge, KnowledgeNode
from services import graph_query_service as query
from scripts.knowledge_graph_phase2_eval import evaluate


FIXTURE = Path(__file__).parent / "fixtures" / "knowledge_graph_phase0.json"


class GraphQueryServiceTests(unittest.TestCase):
    def setUp(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([KnowledgeNode(
                id=node["id"], title=node["title"], content=node["title"],
                node_type=node["node_type"], tags=node.get("tags", []),
                hidden=node["hidden"], promotion_status=node["promotion_status"],
            ) for node in payload["nodes"]])
            db.add_all([KnowledgeEdge(
                id=edge["id"], source_id=edge["source"], target_id=edge["target"],
                relation_type=edge["relation_type"], weight=edge["weight"], origin="legacy",
            ) for edge in payload["edges"]])
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_phase0_fixture_queries_match_stable_expected_results(self):
        with self.sessions() as db:
            self.assertEqual(
                [n["id"] for n in query.get_neighbors(db, "concept:shared")["nodes"]],
                ["concept:root", "paper:a", "paper:b"],
            )
            self.assertEqual(
                [n["id"] for n in query.shortest_path(db, "paper:a", "paper:b")["nodes"]],
                ["paper:a", "concept:data", "paper:b"],
            )
            self.assertEqual(
                [n["id"] for n in query.find_shared_neighbors(db, ["paper:a", "paper:b"])["nodes"]],
                ["concept:data", "concept:shared"],
            )
            self.assertEqual(
                [n["id"] for n in query.find_papers_for_concept(db, "concept:shared")["nodes"]],
                ["paper:a", "paper:b"],
            )
            self.assertFalse(query.shortest_path(db, "paper:a", "paper:c")["found"])

    def test_curated_visibility_and_alias_search_are_enforced(self):
        with self.sessions() as db:
            self.assertEqual(
                [n["id"] for n in query.find_nodes(db, "Shared Encoder")["nodes"]],
                ["concept:shared"],
            )
            self.assertEqual(
                [n["id"] for n in query.find_nodes(db, "shared-encoder-alias")["nodes"]],
                ["concept:shared"],
            )
            self.assertEqual(query.find_nodes(db, "hidden-encoder-alias")["nodes"], [])
            diagnostic = query.find_nodes(db, "hidden-encoder-alias", include_diagnostics=True)
            self.assertEqual([n["id"] for n in diagnostic["nodes"]], ["concept:hidden"])

    def test_limits_and_edge_explanation_are_bounded_and_provenanced(self):
        with self.sessions() as db:
            result = query.get_neighbors(db, "paper:a", depth=999, limit=999)
            self.assertLessEqual(result["depth"], query.MAX_DEPTH)
            self.assertLessEqual(result["count"], query.MAX_RESULTS)
            explanation = query.explain_edge(db, "edge:1")
            self.assertEqual(explanation["edge"]["origin"], "legacy")
            self.assertEqual(explanation["source"]["id"], "paper:a")

    def test_phase2_offline_eval_covers_structure_and_ask_contracts(self):
        report = evaluate(FIXTURE, iterations=1)
        self.assertEqual(report["structure_quality"]["passed"], 8)
        self.assertEqual(report["ask_evidence_readiness"]["passed"], 6)
        self.assertEqual(report["cost"]["graph_query_model_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
