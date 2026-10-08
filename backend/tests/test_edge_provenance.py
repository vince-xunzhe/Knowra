from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import migrate_edge_provenance_columns
from models import Base, KnowledgeEdge, KnowledgeNode, Paper
from services.edge_provenance import (
    EDGE_ORIGINS,
    EdgeSpec,
    add_edge,
    backfill_and_merge_edge_provenance,
    edge_contributions,
    remove_paper_edge_provenance,
)
from services.graph_service import (
    add_nodes_from_paper_extraction,
    get_graph_data,
    remove_nodes_for_paper,
)


class EdgeProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def _node(node_id: str, *, node_type: str = "concept", paper_ids=None):
        return KnowledgeNode(
            id=node_id,
            title=node_id,
            content=node_id,
            node_type=node_type,
            promotion_status="promoted",
            source_paper_ids=list(paper_ids or []),
        )

    def test_structured_writer_covers_all_origins_and_rejects_invalid_values(self):
        with self.sessions() as db:
            nodes = [self._node(f"n{i}") for i in range(6)]
            db.add_all(nodes)
            db.flush()
            for index, origin in enumerate(sorted(EDGE_ORIGINS), start=1):
                result = add_edge(db, EdgeSpec(
                    source_id="n0",
                    target_id=f"n{index}",
                    relation_type=f"relation_{index}",
                    origin=origin,
                    confidence=0.5,
                    evidence="short evidence",
                ))
                self.assertTrue(result.created)
            db.commit()
            self.assertEqual(
                {edge.origin for edge in db.query(KnowledgeEdge).all()},
                EDGE_ORIGINS,
            )

        with self.assertRaises(ValueError):
            EdgeSpec("a", "b", "related", "unknown")
        with self.assertRaises(ValueError):
            EdgeSpec("a", "b", "related", "explicit", confidence=1.1)

    def test_shared_paper_support_survives_targeted_reprocessing(self):
        with self.sessions() as db:
            db.add_all([self._node("a"), self._node("b")])
            db.flush()
            for paper_id in ("paper-1", "paper-2"):
                add_edge(db, EdgeSpec(
                    source_id="a",
                    target_id="b",
                    relation_type="builds_on",
                    origin="explicit",
                    source_paper_id=paper_id,
                    source_field="techniques[].builds_on",
                    evidence=f"support from {paper_id}",
                ))
            db.commit()

            edge = db.query(KnowledgeEdge).one()
            self.assertEqual(
                {item["source_paper_id"] for item in edge_contributions(edge)},
                {"paper-1", "paper-2"},
            )

            report = remove_paper_edge_provenance(db, "paper-1")
            self.assertEqual(report["updated_edges"], 1)
            self.assertEqual(db.query(KnowledgeEdge).count(), 1)
            self.assertEqual(
                edge_contributions(db.query(KnowledgeEdge).one())[0]["source_paper_id"],
                "paper-2",
            )

            report = remove_paper_edge_provenance(db, "paper-2")
            self.assertEqual(report["deleted_edges"], 1)
            self.assertEqual(db.query(KnowledgeEdge).count(), 0)

    def test_manual_contribution_is_not_deleted_by_paper_reprocessing(self):
        with self.sessions() as db:
            db.add_all([self._node("a"), self._node("b")])
            db.flush()
            add_edge(db, EdgeSpec("a", "b", "related", "manual"))
            add_edge(db, EdgeSpec(
                "a", "b", "related", "explicit", source_paper_id="paper-1"
            ))
            db.commit()

            remove_paper_edge_provenance(db, "paper-1")
            edge = db.query(KnowledgeEdge).one()
            self.assertEqual(edge.origin, "manual")
            self.assertEqual(len(edge_contributions(edge)), 1)

    def test_full_paper_reprocess_keeps_other_paper_support(self):
        with self.sessions() as db:
            db.add_all([
                self._node("a", paper_ids=["paper-1", "paper-2"]),
                self._node("b", paper_ids=["paper-1", "paper-2"]),
            ])
            db.flush()
            for paper_id in ("paper-1", "paper-2"):
                add_edge(db, EdgeSpec(
                    "a", "b", "builds_on", "explicit",
                    source_paper_id=paper_id,
                    source_field="techniques[].builds_on",
                ))
            db.commit()

            removed_nodes = remove_nodes_for_paper(db, "paper-1")
            self.assertEqual(removed_nodes, 2)
            self.assertEqual(db.query(KnowledgeNode).count(), 2)
            self.assertTrue(all(
                node.source_paper_ids == ["paper-2"]
                for node in db.query(KnowledgeNode).all()
            ))
            edge = db.query(KnowledgeEdge).one()
            self.assertEqual(
                [item["source_paper_id"] for item in edge_contributions(edge)],
                ["paper-2"],
            )

    def test_backfill_merges_exact_and_reverse_similarity_duplicates(self):
        with self.sessions() as db:
            db.add_all([self._node("a"), self._node("b")])
            db.flush()
            db.add_all([
                KnowledgeEdge(
                    id="e1", source_id="a", target_id="b",
                    relation_type="uses", weight=0.5, origin="legacy",
                ),
                KnowledgeEdge(
                    id="e2", source_id="a", target_id="b",
                    relation_type="uses", weight=0.8, origin="explicit",
                    source_paper_id="paper-1",
                ),
                KnowledgeEdge(
                    id="e3", source_id="a", target_id="b",
                    relation_type="similar", weight=0.7, origin="embedding",
                    confidence=0.7,
                ),
                KnowledgeEdge(
                    id="e4", source_id="b", target_id="a",
                    relation_type="similar", weight=0.9, origin="embedding",
                    confidence=0.9,
                ),
            ])
            db.commit()

            first = backfill_and_merge_edge_provenance(db)
            second = backfill_and_merge_edge_provenance(db)
            self.assertEqual(first["merged_rows"], 2)
            self.assertEqual(second["merged_rows"], 0)
            self.assertEqual(db.query(KnowledgeEdge).count(), 2)
            uses = db.query(KnowledgeEdge).filter_by(relation_type="uses").one()
            self.assertEqual(uses.origin, "explicit")
            self.assertEqual(len(edge_contributions(uses)), 2)
            similar = db.query(KnowledgeEdge).filter_by(relation_type="similar").one()
            self.assertEqual(similar.weight, 0.9)
            self.assertEqual(similar.confidence, 0.9)
            self.assertEqual(len(edge_contributions(similar)), 2)

    def test_sqlite_column_migration_is_idempotent_and_conservative(self):
        engine = create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE TABLE knowledge_edges ("
                    "id TEXT PRIMARY KEY, source_id TEXT, target_id TEXT, "
                    "relation_type TEXT, weight REAL)"
                ))
                conn.execute(text(
                    "INSERT INTO knowledge_edges VALUES "
                    "('s','a','b','similar',0.75),"
                    "('m','a','c','curated_link',1.0),"
                    "('l','a','d','uses',1.0)"
                ))
                migrate_edge_provenance_columns(conn)
                migrate_edge_provenance_columns(conn)
            with engine.connect() as conn:
                columns = {
                    row[1] for row in conn.execute(text("PRAGMA table_info(knowledge_edges)"))
                }
                self.assertTrue({
                    "origin", "confidence", "source_paper_id", "source_field",
                    "evidence", "metadata", "extractor_version",
                }.issubset(columns))
                rows = {
                    row.id: row
                    for row in conn.execute(text(
                        "SELECT id, origin, confidence FROM knowledge_edges"
                    )).mappings()
                }
                self.assertEqual(rows["s"].origin, "embedding")
                self.assertEqual(rows["s"].confidence, 0.75)
                self.assertEqual(rows["m"].origin, "manual")
                self.assertEqual(rows["l"].origin, "legacy")
        finally:
            engine.dispose()

    def test_paper_extraction_edges_include_source_field_and_paper(self):
        extraction = {
            "title": "Synthetic Paper",
            "abstract_summary": "A paper.",
            "problem_area": "Graph learning",
            "techniques": [
                {"name": "Method A", "role": "encoder", "builds_on": ["Method B"]},
                {"name": "Method B", "role": "backbone", "builds_on": []},
            ],
            "datasets": [{"name": "Dataset X", "purpose": "training"}],
            "baselines": ["Baseline Y"],
            "keywords": [],
        }
        embedding_keys = {
            ("paper", "Synthetic Paper"),
            ("problem_area", "Graph learning"),
            ("technique", "Method A"),
            ("technique", "Method B"),
            ("dataset", "Dataset X"),
            ("technique", "Baseline Y"),
        }
        with self.sessions() as db:
            db.add(Paper(
                id="paper-1", filepath="paper.pdf", filename="paper.pdf",
                file_hash="hash", processed=True,
            ))
            db.flush()
            add_nodes_from_paper_extraction(
                extraction,
                "paper-1",
                "",
                "embedding-test",
                1.1,
                db,
                embeddings={key: None for key in embedding_keys},
            )
            db.commit()
            edges = db.query(KnowledgeEdge).filter(
                KnowledgeEdge.origin == "explicit"
            ).all()
            self.assertGreaterEqual(len(edges), 5)
            self.assertTrue(all(edge.source_paper_id == "paper-1" for edge in edges))
            self.assertTrue(all(edge.source_field for edge in edges))
            graph = get_graph_data(db, include_candidates=True)
            self.assertTrue(all("origin" in edge for edge in graph["edges"]))
            self.assertTrue(all("metadata" in edge for edge in graph["edges"]))

    def test_manual_concept_synthetic_edge_is_inspectable(self):
        with self.sessions() as db:
            db.add(Paper(
                id="paper-1", filepath="paper.pdf", filename="paper.pdf",
                file_hash="hash", processed=True,
            ))
            db.add_all([
                self._node("paper-node", node_type="paper", paper_ids=["paper-1"]),
                KnowledgeNode(
                    id="manual-node",
                    title="Manual concept",
                    content="User-authored concept",
                    node_type="concept",
                    node_origin="manual",
                    promotion_status="promoted",
                    source_paper_ids=["paper-1"],
                ),
            ])
            db.commit()
            graph = get_graph_data(db)
            edge = next(item for item in graph["edges"] if item["id"].startswith("manual:"))
            self.assertEqual(edge["origin"], "manual")
            self.assertEqual(edge["source_paper_id"], "paper-1")
            self.assertEqual(edge["source_field"], "manual_concept.paper_ids")
            self.assertTrue(edge["metadata"]["synthetic"])

    def test_supabase_migration_keeps_rls_and_validation_contracts(self):
        sql = (
            Path(__file__).resolve().parents[2]
            / "supabase" / "migrations" / "0010_edge_provenance.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS origin", sql)
        self.assertIn("source_paper_id UUID REFERENCES papers(id)", sql)
        self.assertIn("knowledge_edges_origin_check", sql)
        self.assertIn("knowledge_edges_confidence_check", sql)
        self.assertIn("source paper user mismatch", sql)
        # The original migration remains the authority for tenant isolation
        # and cross-user node validation; Phase 1 must not weaken either.
        base_sql = (
            Path(__file__).resolve().parents[2]
            / "supabase" / "migrations" / "0003_knowledge.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("edge_user_consistency_check", base_sql)
        self.assertIn("ALTER TABLE knowledge_edges ENABLE ROW LEVEL SECURITY", base_sql)


if __name__ == "__main__":
    unittest.main()
