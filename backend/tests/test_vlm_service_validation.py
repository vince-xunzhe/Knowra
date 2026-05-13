import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.vlm_service import (
    extract_knowledge_from_paper,
    extraction_has_critical_issues,
    extraction_quality_issues,
    run_chat_turn,
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

    @patch("services.vlm_service._resolve_image_paths_for_pages")
    @patch("services.vlm_service.extract_text_pages")
    @patch("services.vlm_service.call_text_model")
    @patch("services.vlm_service._resolve_model_gateway_context", return_value=({}, "codex_cli"))
    def test_extract_can_use_codex_cli_local_context(
        self,
        mock_gateway_context,
        mock_call_text_model,
        mock_extract_text_pages,
        mock_resolve_image_paths,
    ):
        image_path = Path(__file__).with_name("tmp-codex-paper.png")
        image_path.write_text("fake", encoding="utf-8")
        mock_extract_text_pages.return_value = (
            [
                {"page_number": 1, "text": "Title: Codex Paper\nAuthors: Alice"},
                {"page_number": 2, "text": "Method: Transformer\nDataset: ImageNet"},
            ],
            8,
        )
        mock_resolve_image_paths.return_value = [str(image_path)]
        final_raw = (
            '{"title":"Codex Paper","authors":["Alice"],"problem_area":"视觉",'
            '"abstract_summary":"摘要","techniques":[{"name":"Transformer","role":"主干","aliases":[],"builds_on":[]}],'
            '"datasets":[{"name":"ImageNet","purpose":"评测"}],"baselines":["CLIP"],'
            '"key_findings":[{"short":"更强","detail":"提升明显"}]}'
        )
        mock_call_text_model.side_effect = [
            "- 标题：Codex Paper\n- 作者：Alice\n- 方法：Transformer\n- 数据集：ImageNet",
            final_raw,
        ]

        try:
            parsed, raw, file_id, assistant_id, thread_id, vector_store_id = extract_knowledge_from_paper(
                pdf_filepath="dummy.pdf",
                prompt="PROMPT",
                api_key="",
                model="codex-cli/gpt-5.4",
                fallback_text="Codex Paper\nAlice",
                first_page_image_path=str(image_path),
                file_hash="hash-123",
            )
        finally:
            image_path.unlink(missing_ok=True)

        self.assertEqual(parsed["title"], "Codex Paper")
        self.assertEqual(raw, final_raw)
        self.assertEqual(file_id, "")
        self.assertEqual(assistant_id, "")
        self.assertEqual(vector_store_id, "")
        self.assertTrue(thread_id.startswith("local-codex-extract:"))
        self.assertEqual(mock_call_text_model.call_count, 2)
        final_call = mock_call_text_model.call_args_list[-1].kwargs
        self.assertIn("[CHUNK_NOTES]", final_call["user"])
        self.assertIn("Chunk 1", final_call["user"])
        self.assertEqual(final_call["image_paths"], [str(image_path)])

    @patch("services.vlm_service.call_text_model", return_value="这是 Codex 对论文的回答。")
    @patch("services.vlm_service._resolve_model_gateway_context", return_value=({}, "codex_cli"))
    def test_run_chat_turn_can_use_codex_cli_local_context(
        self,
        mock_gateway_context,
        mock_call_text_model,
    ):
        image_path = Path(__file__).with_name("tmp-codex-chat.png")
        image_path.write_text("fake", encoding="utf-8")

        try:
            reply, thread_id, was_recreated, vector_store_id = run_chat_turn(
                api_key="",
                model="codex-cli/gpt-5.4",
                assistant_id="",
                file_id="",
                user_message="这篇论文的核心贡献是什么？",
                chat_history=[{"role": "user", "content": "先给我总览"}],
                paper_title="Codex Paper",
                paper_notes="这篇论文挺关键。",
                paper_raw_llm_response='{"title":"Codex Paper","authors":["Alice"]}',
                paper_extracted_text="论文提出了一个新的视觉模型。",
                first_page_image_path=str(image_path),
            )
        finally:
            image_path.unlink(missing_ok=True)

        self.assertEqual(reply, "这是 Codex 对论文的回答。")
        self.assertTrue(thread_id.startswith("local-codex-chat:"))
        self.assertTrue(was_recreated)
        self.assertEqual(vector_store_id, "")
        self.assertIn("[STRUCTURED_EXTRACTION]", mock_call_text_model.call_args.kwargs["user"])
        self.assertIn("Codex Paper", mock_call_text_model.call_args.kwargs["user"])
        self.assertEqual(mock_call_text_model.call_args.kwargs["image_paths"], [str(image_path)])

    @patch("services.vlm_service._resolve_image_paths_for_pages")
    @patch("services.vlm_service.extract_text_pages")
    @patch("services.vlm_service.call_text_model")
    @patch("services.vlm_service._resolve_model_gateway_context", return_value=({}, "codex_cli"))
    def test_extract_retries_with_lighter_context_after_codex_timeout(
        self,
        mock_gateway_context,
        mock_call_text_model,
        mock_extract_text_pages,
        mock_resolve_image_paths,
    ):
        image_paths = ["img1.png", "img2.png", "img3.png"]
        mock_extract_text_pages.return_value = (
            [{"page_number": 1, "text": "Title: Codex Paper\nAuthors: Alice"}],
            12,
        )
        mock_resolve_image_paths.return_value = image_paths
        final_raw = (
            '{"title":"Codex Paper","authors":["Alice"],"problem_area":"视觉",'
            '"abstract_summary":"摘要","techniques":[{"name":"Transformer","role":"主干","aliases":[],"builds_on":[]}],'
            '"datasets":[{"name":"ImageNet","purpose":"评测"}],"baselines":["CLIP"],'
            '"key_findings":[{"short":"更强","detail":"提升明显"}]}'
        )
        mock_call_text_model.side_effect = [
            "- 标题：Codex Paper\n- 作者：Alice",
            Exception("Codex CLI request timed out"),
            final_raw,
        ]

        parsed, raw, *_ = extract_knowledge_from_paper(
            pdf_filepath="dummy.pdf",
            prompt="PROMPT",
            api_key="",
            model="codex-cli/gpt-5.4",
            fallback_text="Codex Paper\nAlice",
            file_hash="hash-123",
        )

        self.assertEqual(parsed["title"], "Codex Paper")
        self.assertEqual(raw, final_raw)
        self.assertEqual(mock_call_text_model.call_count, 3)
        first_final = mock_call_text_model.call_args_list[1].kwargs
        second_final = mock_call_text_model.call_args_list[2].kwargs
        self.assertEqual(first_final["image_paths"], image_paths)
        self.assertEqual(second_final["image_paths"], image_paths[:2])
        self.assertEqual(first_final["timeout_s"], 600)
        self.assertEqual(second_final["timeout_s"], 420)


if __name__ == "__main__":
    unittest.main()
