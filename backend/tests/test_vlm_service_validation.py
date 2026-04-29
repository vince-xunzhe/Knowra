import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.vlm_service import (
    extract_knowledge_from_paper,
    extraction_has_critical_issues,
    extraction_quality_issues,
)


class ExtractionValidationTests(unittest.TestCase):
    def test_empty_shell_is_reported_as_critical(self):
        extraction = {
            "title": "",
            "authors": [],
            "problem_area": "",
            "core_contribution": "",
            "abstract_summary": "",
            "problem": "",
            "motivation": "",
            "principle": {
                "analogy": "",
                "architecture_flow": "",
                "key_formulas": [{"name": "", "formula": "", "plain": ""}],
            },
            "innovations": {
                "previous_work": "",
                "this_work": "",
                "why_better": "",
            },
            "experimental_gains": "",
            "historical_position": {
                "builds_on": "",
                "inspired": "",
                "overall": "",
            },
            "limitations": "",
            "pytorch_snippet": {
                "module_name": "",
                "code": "",
                "notes": "",
            },
            "techniques": [],
            "datasets": [],
            "baselines": [],
            "contributions": [],
            "key_findings": [{"short": "", "detail": ""}],
        }

        issues = extraction_quality_issues(extraction)

        self.assertIn("title 为空", issues)
        self.assertIn("authors 为空", issues)
        self.assertIn("图谱关键字段全空", issues)
        self.assertIn("叙事字段全空", issues)
        self.assertTrue(extraction_has_critical_issues(extraction))

    def test_blank_placeholder_objects_do_not_count_as_graph_payload(self):
        extraction = {
            "title": "Example Paper",
            "authors": ["Alice"],
            "problem_area": "",
            "abstract_summary": "这是一段正常摘要。",
            "techniques": [{"name": "", "aliases": [], "role": "", "builds_on": []}],
            "datasets": [{"name": "", "purpose": ""}],
            "baselines": [""],
            "key_findings": [{"short": "", "detail": ""}],
        }

        issues = extraction_quality_issues(extraction)

        self.assertIn("图谱关键字段全空", issues)
        self.assertTrue(extraction_has_critical_issues(extraction))

    def test_nonempty_extraction_passes_validation(self):
        extraction = {
            "title": "Example Paper",
            "authors": ["Alice", "Bob"],
            "problem_area": "多模态",
            "abstract_summary": "论文提出一个新的多模态方法。",
            "techniques": [
                {
                    "name": "Transformer",
                    "aliases": ["ViT"],
                    "role": "主干网络",
                    "builds_on": [],
                }
            ],
            "datasets": [{"name": "ImageNet", "purpose": "评测"}],
            "baselines": ["CLIP"],
            "key_findings": [{"short": "更强", "detail": "top-1 从 76 提升到 80。"}],
        }

        self.assertEqual(extraction_quality_issues(extraction), [])
        self.assertFalse(extraction_has_critical_issues(extraction))

    @patch("services.vlm_service._run_extraction_once")
    @patch("services.vlm_service._ensure_file", return_value="file-123")
    @patch("services.vlm_service.OpenAI")
    def test_extract_retries_with_local_text_fallback(
        self,
        mock_openai,
        mock_ensure_file,
        mock_run_once,
    ):
        empty_shell = {
            "title": "",
            "authors": [],
            "problem_area": "",
            "abstract_summary": "",
            "techniques": [],
            "datasets": [],
            "baselines": [],
            "key_findings": [],
        }
        recovered = {
            "title": "Example Paper",
            "authors": ["Alice", "Bob"],
            "problem_area": "多模态",
            "abstract_summary": "论文提出一个新的多模态方法。",
            "techniques": [
                {"name": "Transformer", "aliases": [], "role": "主干网络", "builds_on": []}
            ],
            "datasets": [{"name": "ImageNet", "purpose": "评测"}],
            "baselines": ["CLIP"],
            "key_findings": [{"short": "更强", "detail": "top-1 从 76 提升到 80。"}],
        }
        mock_openai.return_value = MagicMock()
        mock_run_once.side_effect = [
            (empty_shell, '{"title":""}', "", "resp-empty", "vs-1"),
            (recovered, '{"title":"Example Paper"}', "", "resp-good", "vs-1"),
        ]

        parsed, raw, file_id, assistant_id, thread_id, vector_store_id = extract_knowledge_from_paper(
            pdf_filepath="dummy.pdf",
            prompt="PROMPT",
            api_key="test-key",
            model="gpt-5.4",
            fallback_text="Example Paper\nAlice\nBob",
        )

        self.assertEqual(parsed["title"], "Example Paper")
        self.assertEqual(raw, '{"title":"Example Paper"}')
        self.assertEqual(file_id, "file-123")
        self.assertEqual(thread_id, "resp-good")
        self.assertEqual(vector_store_id, "vs-1")
        self.assertEqual(assistant_id, "")
        self.assertEqual(mock_run_once.call_count, 2)

        second_prompt = mock_run_once.call_args_list[1].args[3]
        self.assertIn("LOCAL_PDF_TEXT_BEGIN", second_prompt)
        self.assertIn("Example Paper\nAlice\nBob", second_prompt)
        mock_ensure_file.assert_called_once()


if __name__ == "__main__":
    unittest.main()
