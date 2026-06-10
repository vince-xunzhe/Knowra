import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import Base, KnowledgeEdge, KnowledgeNode, Paper
from services.graph_service import (
    _add_similarity_edges,
    _find_existing_paper_node,
    _serialize_graph_node,
    get_graph_data,
    is_publishable_concept_node,
    rebuild_similarity_edges,
    repair_merged_paper_nodes,
)
from services.wiki_compiler import compile_paper_page


def _node(**overrides):
    base = dict(
        node_type="technique",
        node_origin="auto",
        hidden=False,
        promotion_status="promoted",
        source_paper_ids=[1, 2],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class GraphCurationTests(unittest.TestCase):
    def test_promoted_node_is_publishable(self):
        self.assertTrue(is_publishable_concept_node(_node(), {1, 2}))

    def test_pending_node_is_not_publishable(self):
        self.assertFalse(
            is_publishable_concept_node(_node(promotion_status="pending"), {1, 2})
        )

    def test_rejected_node_is_not_publishable(self):
        self.assertFalse(
            is_publishable_concept_node(_node(promotion_status="rejected"), {1, 2})
        )

    def test_hidden_overrides_promoted(self):
        self.assertFalse(
            is_publishable_concept_node(_node(hidden=True), {1, 2})
        )

    def test_paper_node_is_not_a_concept(self):
        self.assertFalse(
            is_publishable_concept_node(_node(node_type="paper"), {1, 2})
        )

    def test_finding_node_is_not_a_concept(self):
        self.assertFalse(
            is_publishable_concept_node(_node(node_type="finding"), {1, 2})
        )

    def test_promoted_node_with_no_processed_paper_drops_out(self):
        self.assertFalse(
            is_publishable_concept_node(_node(source_paper_ids=[1]), {2})
        )

    @patch("services.graph_service._add_edge")
    @patch("services.graph_service.cosine_similarity", return_value=0.9)
    def test_similarity_edges_skip_nodes_without_embedding(
        self,
        mock_cosine,
        mock_add_edge,
    ):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [
            SimpleNamespace(id=2, embedding=None),
            SimpleNamespace(id=3, embedding=[1.0, 0.0]),
        ]

        _add_similarity_edges(
            db,
            SimpleNamespace(id=1, embedding=[1.0, 0.0]),
            0.6,
        )

        mock_cosine.assert_called_once_with([1.0, 0.0], [1.0, 0.0])
        mock_add_edge.assert_called_once_with(db, 1, 3, "similar", 0.9)

    def test_find_existing_paper_node_prefers_single_source_title_match(self):
        db = MagicMock()
        db.query.return_value.all.return_value = [
            SimpleNamespace(
                id=1,
                node_type="paper",
                title="N3D-VLM: Native 3D Grou…",
                source_paper_ids=[1, 3, 14, 24],
            ),
            SimpleNamespace(
                id=303,
                node_type="paper",
                title="DriveLM: Driving with G…",
                source_paper_ids=[14],
            ),
        ]

        found = _find_existing_paper_node(
            db,
            paper_id=14,
            title="DriveLM: Driving with Graph Visual Question Answering",
        )

        self.assertIsNotNone(found)
        self.assertEqual(found.id, 303)

    def test_serialize_graph_node_exposes_explicit_paper_id(self):
        node = SimpleNamespace(
            id=42,
            title="DriveLM",
            content="demo",
            node_type="paper",
            node_origin="auto",
            hidden=False,
            promotion_status="promoted",
            promoted_by=None,
            promotion_reason=None,
            last_promotion_eval_at=None,
            tags=[],
            source_paper_ids=["14"],
            created_at=None,
        )

        data = _serialize_graph_node(node, {"14"})

        self.assertEqual(data["id"], "42")
        self.assertEqual(data["paper_id"], "14")
        self.assertIsNone(data["concept_id"])

    def test_repair_merged_paper_nodes_reassigns_edges(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            db.add_all(
                [
                    Paper(id="1", filepath="paper-1.pdf", filename="paper-1.pdf", file_hash="h1", title="N3D-VLM: Native 3D Grounding Enables Accurate Spatial Reasoning in Vision-Language Models"),
                    Paper(id="14", filepath="paper-14.pdf", filename="paper-14.pdf", file_hash="h14", title="DriveLM: Driving with Graph Visual Question Answering"),
                ]
            )
            merged = KnowledgeNode(
                id="1",
                title="N3D-VLM: Native 3D Grou…",
                content="merged content",
                node_type="paper",
                node_origin="auto",
                promotion_status="promoted",
                source_paper_ids=["1", "14"],
                embedding=None,
                tags=[],
            )
            drivelm = KnowledgeNode(
                id="303",
                title="DriveLM: Driving with G…",
                content="DriveLM content",
                node_type="paper",
                node_origin="auto",
                promotion_status="promoted",
                source_paper_ids=["14"],
                embedding=None,
                tags=[],
            )
            n3d_only = KnowledgeNode(
                id="3",
                title="结构化语言输出",
                content="belongs to paper 1",
                node_type="technique",
                node_origin="auto",
                promotion_status="promoted",
                source_paper_ids=["1"],
                embedding=None,
                tags=[],
            )
            drivelm_only = KnowledgeNode(
                id="304",
                title="视觉问答",
                content="belongs to paper 14",
                node_type="technique",
                node_origin="auto",
                promotion_status="promoted",
                source_paper_ids=["14"],
                embedding=None,
                tags=[],
            )
            db.add_all([merged, drivelm, n3d_only, drivelm_only])
            db.flush()
            db.add_all(
                [
                    KnowledgeEdge(source_id="1", target_id="3", relation_type="uses", weight=1.0),
                    KnowledgeEdge(source_id="1", target_id="304", relation_type="uses", weight=1.0),
                    KnowledgeEdge(source_id="1", target_id="303", relation_type="uses", weight=1.0),
                ]
            )
            db.commit()

            repaired = repair_merged_paper_nodes(db, similarity_threshold=0.6)

            self.assertEqual(repaired, 1)
            repaired_merged = db.query(KnowledgeNode).filter(KnowledgeNode.id == "1").first()
            self.assertEqual(repaired_merged.source_paper_ids, ["1"])
            self.assertEqual(
                repaired_merged.title,
                "N3D-VLM: Native 3D Grounding Enables Accurate Spatial Reasoning in Vision-Language Models",
            )

            edges = {
                (edge.source_id, edge.target_id, edge.relation_type)
                for edge in db.query(KnowledgeEdge).all()
            }
            self.assertIn(("1", "3", "uses"), edges)
            self.assertNotIn(("1", "304", "uses"), edges)
            self.assertIn(("303", "304", "uses"), edges)
            self.assertNotIn(("1", "303", "uses"), edges)
        finally:
            db.close()
            engine.dispose()

    def test_rebuild_similarity_edges_summary_preserves_non_similar_and_manual_nodes(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            n1 = KnowledgeNode(
                id="1",
                title="Node 1",
                content="n1",
                node_type="technique",
                node_origin="auto",
                promotion_status="promoted",
                source_paper_ids=["1"],
                embedding=[1.0, 0.0],
                tags=[],
            )
            n2 = KnowledgeNode(
                id="2",
                title="Node 2",
                content="n2",
                node_type="technique",
                node_origin="auto",
                promotion_status="promoted",
                source_paper_ids=["2"],
                embedding=[1.0, 0.0],
                tags=[],
            )
            n3 = KnowledgeNode(
                id="3",
                title="Node 3",
                content="n3",
                node_type="technique",
                node_origin="auto",
                promotion_status="promoted",
                source_paper_ids=["3"],
                embedding=[0.0, 1.0],
                tags=[],
            )
            manual_node = KnowledgeNode(
                id="4",
                title="Manual Concept",
                content="manual",
                node_type="concept",
                node_origin="manual",
                promotion_status="promoted",
                source_paper_ids=[],
                embedding=None,
                tags=[],
            )
            db.add_all([n1, n2, n3, manual_node])
            db.flush()
            db.add_all(
                [
                    KnowledgeEdge(source_id="1", target_id="3", relation_type="similar", weight=0.95),
                    KnowledgeEdge(source_id="1", target_id="4", relation_type="curated_link", weight=1.0),
                ]
            )
            db.commit()

            summary = rebuild_similarity_edges(db, threshold=0.8)

            self.assertEqual(summary["total_nodes"], 4)
            self.assertEqual(summary["embedding_nodes"], 3)
            self.assertEqual(summary["candidate_edges"], 3)
            self.assertEqual(summary["final_edges"], 1)
            self.assertEqual(summary["threshold"], 0.8)
            self.assertEqual(summary["removed_similar_edges"], 1)

            manual_still_exists = (
                db.query(KnowledgeNode)
                .filter(KnowledgeNode.id == "4", KnowledgeNode.node_origin == "manual")
                .first()
            )
            self.assertIsNotNone(manual_still_exists)

            edges = db.query(KnowledgeEdge).all()
            similar_edges = [e for e in edges if e.relation_type == "similar"]
            curated_edges = [e for e in edges if e.relation_type == "curated_link"]

            self.assertEqual(len(similar_edges), 1)
            self.assertEqual(
                {similar_edges[0].source_id, similar_edges[0].target_id},
                {"1", "2"},
            )
            self.assertEqual(len(curated_edges), 1)
            self.assertEqual((curated_edges[0].source_id, curated_edges[0].target_id), ("1", "4"))
        finally:
            db.close()
            engine.dispose()

    def test_get_graph_data_edges_include_created_at(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            n1 = KnowledgeNode(
                id=1,
                title="Paper A",
                content="a",
                node_type="paper",
                node_origin="auto",
                promotion_status="promoted",
                source_paper_ids=[1],
                embedding=[1.0, 0.0],
                tags=[],
            )
            n2 = KnowledgeNode(
                id=2,
                title="Paper B",
                content="b",
                node_type="paper",
                node_origin="auto",
                promotion_status="promoted",
                source_paper_ids=[2],
                embedding=[0.9, 0.1],
                tags=[],
            )
            db.add_all([n1, n2])
            db.flush()
            db.add(KnowledgeEdge(source_id=1, target_id=2, relation_type="similar", weight=0.9))
            db.commit()

            payload = get_graph_data(db)
            similar_edges = [e for e in payload["edges"] if e["relation_type"] == "similar"]

            self.assertEqual(len(similar_edges), 1)
            self.assertIn("created_at", similar_edges[0])
            self.assertIsNotNone(similar_edges[0]["created_at"])
        finally:
            db.close()
            engine.dispose()

class WikiCompileSkipTests(unittest.TestCase):
    def test_compile_paper_page_skips_unchanged_signature(self):
        paper = SimpleNamespace(
            id=1,
            filename="demo-paper.pdf",
            title="Demo Paper",
            authors=["Alice", "Bob"],
            processed=True,
            processed_at=None,
            extraction_model="gpt-5.4",
            raw_llm_response='{"title":"Demo Paper","authors":["Alice","Bob"]}',
            notes="",
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch("services.wiki_compiler.WIKI_PAPERS_DIR", tmp_path):
                with patch("services.wiki_compiler._call_llm", return_value="## 核心贡献\n首次编译正文") as first_call:
                    first_path = compile_paper_page(paper, api_key="test-key", model="gpt-4o-mini")
                self.assertIsNotNone(first_path)
                self.assertEqual(first_call.call_count, 1)

                with patch("services.wiki_compiler._call_llm", side_effect=AssertionError("LLM should have been skipped")):
                    second_path = compile_paper_page(paper, api_key="test-key", model="gpt-4o-mini")

                self.assertEqual(first_path, second_path)
                self.assertTrue((second_path).is_file())


if __name__ == "__main__":
    unittest.main()
