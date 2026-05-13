import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.graph_service import _add_similarity_edges, is_publishable_concept_node
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
