import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.graph_service import is_publishable_concept_node
from services.wiki_compiler import compile_paper_page


class GraphCurationTests(unittest.TestCase):
    def test_auto_concept_needs_two_processed_papers(self):
        node = SimpleNamespace(
            node_type="technique",
            node_origin="auto",
            hidden=False,
            source_paper_ids=[1],
        )
        self.assertFalse(is_publishable_concept_node(node, {1}))
        node.source_paper_ids = [1, 2]
        self.assertTrue(is_publishable_concept_node(node, {1, 2}))

    def test_manual_concept_can_publish_with_single_paper(self):
        node = SimpleNamespace(
            node_type="concept",
            node_origin="manual",
            hidden=False,
            source_paper_ids=[7],
        )
        self.assertTrue(is_publishable_concept_node(node, {7}))

    def test_hidden_node_is_not_publishable(self):
        node = SimpleNamespace(
            node_type="concept",
            node_origin="manual",
            hidden=True,
            source_paper_ids=[7, 8],
        )
        self.assertFalse(is_publishable_concept_node(node, {7, 8}))


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
