from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import schema_preflight_report
from models import Base, KnowledgeEdge, KnowledgeNode, Paper
from services.edge_provenance import EdgeSpec, add_edge
from services.graph_analysis_service import analyze_graph
from services.graph_audit_service import diff_graphs, export_graph, graph_signature
from services.graph_mutation_service import (
    apply_mutation_plan,
    fragment_from_extraction,
    resolve_mutation_plan,
)
from services.pipeline_manifest import (
    extraction_is_reusable,
    extract_deterministic_identity,
    invalidate_from,
    load_manifest,
    record_extraction,
)


def _extraction(title="New Paper"):
    return {
        "title": title,
        "authors": ["A"],
        "abstract_summary": "Summary",
        "venue": "arXiv preprint",
        "year": 2026,
        "problem_area": "Graph Learning",
        "keywords": ["graphs"],
        "techniques": [
            {"name": "New Method", "aliases": ["NM"], "role": "backbone", "builds_on": []}
        ],
        "datasets": [],
        "baselines": [],
    }


class ManifestTests(unittest.TestCase):
    def test_manifest_hash_checkpoint_identity_and_invalidation(self):
        paper = SimpleNamespace(
            id="p1",
            filename="2601.01234v2.pdf",
            file_hash="abc",
            raw_llm_response=json.dumps(_extraction()),
        )
        cfg = {"extraction_prompt": "prompt-v1"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_extraction(
                paper,
                cfg,
                "model-a",
                _extraction(),
                paper.raw_llm_response,
                root=root,
            )
            self.assertTrue(
                extraction_is_reusable(paper, cfg, "model-a", root=root)
            )
            self.assertFalse(
                extraction_is_reusable(
                    paper, {"extraction_prompt": "prompt-v2"}, "model-a", root=root
                )
            )
            manifest = load_manifest("p1", root=root)
            self.assertEqual(manifest["identity"]["canonical_id"], "arxiv:2601.01234")
            invalidate_from("p1", "extraction", root=root)
            self.assertFalse(load_manifest("p1", root=root)["layers"])

    def test_doi_identity_wins_over_arxiv_and_hash(self):
        identity = extract_deterministic_identity(
            filename="2601.01234.pdf",
            file_hash="abc",
            raw_text="Published as doi:10.1234/ABC.567",
        )
        self.assertEqual(identity["canonical_id"], "doi:10.1234/abc.567")
        self.assertEqual(identity["arxiv_id"], "2601.01234")


class GraphMutationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(Paper(
                id="p1",
                filepath="papers/old.pdf",
                filename="old.pdf",
                file_hash="hash",
                processed=True,
            ))
            db.add_all([
                KnowledgeNode(
                    id="paper-old",
                    title="Old Paper",
                    content="old",
                    node_type="paper",
                    source_paper_ids=["p1"],
                    promotion_status="promoted",
                ),
                KnowledgeNode(
                    id="concept-old",
                    title="Old Method",
                    content="old",
                    node_type="technique",
                    source_paper_ids=["p1"],
                    promotion_status="promoted",
                ),
                KnowledgeNode(
                    id="manual",
                    title="Manual",
                    content="manual",
                    node_type="concept",
                    node_origin="manual",
                    source_paper_ids=["p1"],
                    promotion_status="promoted",
                ),
            ])
            db.flush()
            add_edge(db, EdgeSpec(
                source_id="paper-old",
                target_id="concept-old",
                relation_type="uses",
                origin="explicit",
                source_paper_id="p1",
                source_field="techniques",
            ))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def _plan(self, db):
        fragment = fragment_from_extraction(
            _extraction(), paper_id="p1", source_sha256="hash"
        )
        return resolve_mutation_plan(
            db,
            fragment,
            embeddings={
                ("paper", "New Paper"): [1.0, 0.0],
                ("problem_area", "Graph Learning"): [0.0, 1.0],
                ("technique", "New Method"): [0.5, 0.5],
            },
            similarity_threshold=0.99,
        )

    def test_dry_run_is_read_only_and_apply_is_idempotent(self):
        with self.sessions() as db:
            plan = self._plan(db)
            before = (db.query(KnowledgeNode).count(), db.query(KnowledgeEdge).count())
            preview = apply_mutation_plan(db, plan, dry_run=True)
            self.assertTrue(preview["dry_run"])
            self.assertEqual(
                before,
                (db.query(KnowledgeNode).count(), db.query(KnowledgeEdge).count()),
            )

            applied = apply_mutation_plan(db, plan)
            db.commit()
            self.assertTrue(applied["applied"])
            self.assertIsNone(db.get(KnowledgeNode, "concept-old"))
            self.assertIsNotNone(db.get(KnowledgeNode, "manual"))
            self.assertEqual(db.get(KnowledgeNode, "paper-old").title, "New Paper")

            second = self._plan(db)
            self.assertEqual(second.create_nodes, [])
            self.assertEqual(second.delete_node_ids, [])
            apply_mutation_plan(db, second)
            db.commit()
            logical = {
                (edge.source_id, edge.target_id, edge.relation_type)
                for edge in db.query(KnowledgeEdge).all()
            }
            self.assertEqual(len(logical), db.query(KnowledgeEdge).count())

    def test_writer_failure_rolls_back_old_graph_slice(self):
        with self.sessions() as db:
            plan = self._plan(db)
            with patch(
                "services.graph_mutation_service.add_edge",
                side_effect=RuntimeError("injected write failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    apply_mutation_plan(db, plan)
            db.expire_all()
            self.assertIsNotNone(db.get(KnowledgeNode, "concept-old"))
            self.assertEqual(db.get(KnowledgeNode, "paper-old").title, "Old Paper")
            self.assertEqual(db.query(KnowledgeEdge).count(), 1)


class AuditAndAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_export_signature_and_diff_ignore_list_order(self):
        with self.sessions() as db:
            db.add_all([
                KnowledgeNode(id="a", title="A", content="", node_type="paper", promotion_status="promoted"),
                KnowledgeNode(id="b", title="B", content="", node_type="concept", promotion_status="promoted"),
            ])
            db.flush()
            add_edge(db, EdgeSpec(
                source_id="a", target_id="b", relation_type="uses",
                origin="explicit", source_paper_id="p1", source_field="techniques",
            ))
            db.commit()
            before = export_graph(db)
            reordered = {
                **before,
                "nodes": list(reversed(before["nodes"])),
                "edges": list(reversed(before["edges"])),
            }
            self.assertEqual(graph_signature(before), graph_signature(reordered))

            db.add(KnowledgeNode(
                id="c", title="C", content="", node_type="concept",
                node_origin="manual", promotion_status="promoted",
            ))
            db.commit()
            after = export_graph(db)
            diff = diff_graphs(reordered, after)
            self.assertTrue(diff["changed"])
            self.assertEqual(diff["counts"]["added_nodes"], 1)
            self.assertEqual(diff["change_sources"]["user"], 1)

    def test_analysis_finds_components_orphans_superhub_and_cache(self):
        with self.sessions() as db, tempfile.TemporaryDirectory() as tmp:
            center = KnowledgeNode(
                id="center", title="Center", content="", node_type="paper",
                promotion_status="promoted",
            )
            leaves = [
                KnowledgeNode(
                    id=f"leaf-{index}", title=f"Leaf {index}", content="",
                    node_type="concept", promotion_status="promoted",
                )
                for index in range(9)
            ]
            orphan = KnowledgeNode(
                id="orphan", title="Orphan", content="", node_type="paper",
                promotion_status="promoted",
            )
            db.add_all([center, *leaves, orphan])
            db.flush()
            for leaf in leaves:
                db.add(KnowledgeEdge(
                    source_id="center",
                    target_id=leaf.id,
                    relation_type="uses",
                    weight=1.0,
                    origin="legacy",
                ))
            db.commit()
            cache = Path(tmp) / "analysis.json"
            first = analyze_graph(db, cache_path=cache)
            second = analyze_graph(db, cache_path=cache)
            self.assertEqual(first["counts"]["components"], 2)
            self.assertEqual(first["counts"]["orphans"], 1)
            self.assertEqual(first["counts"]["super_hubs"], 1)
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["cache_hit"])


class SchemaPreflightTests(unittest.TestCase):
    def test_reports_missing_runtime_schema(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE papers (id VARCHAR PRIMARY KEY)"))
            report = schema_preflight_report(engine)
            self.assertFalse(report["ok"])
            self.assertIn("knowledge_edges", report["missing_tables"])
            self.assertIn("filename", report["missing_columns"]["papers"])
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
