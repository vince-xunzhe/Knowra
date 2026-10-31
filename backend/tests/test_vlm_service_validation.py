import sys
import unittest
import hashlib
import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.vlm_service import (
    PaperExtractionError,
    extract_knowledge_from_paper,
    extraction_has_critical_issues,
    extraction_quality_issues,
    run_chat_turn,
)
from services.paper_evidence_service import build_evidence_manifest


def _evidence_manifest(page_texts):
    source_hash = hashlib.sha256("synthetic-paper".encode()).hexdigest()
    return build_evidence_manifest(
        paper_id="synthetic",
        source_file_hash=source_hash,
        source_size_bytes=sum(len(text) for text in page_texts),
        total_pages=len(page_texts),
        pages=[
            {"page_number": index, "status": "ok", "text": text}
            for index, text in enumerate(page_texts, start=1)
        ],
    )


def _pdf_bytes(*texts):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): writer._add_object(font),
            })
        })
        stream = DecodedStreamObject()
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream.set_data(("BT /F1 12 Tf 20 250 Td (" + escaped + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class ExtractionValidationTests(unittest.TestCase):
    def test_codex_configuration_error_stops_chunk_processing_without_parse_wrapper(self):
        from model_gateway.runtime import ProviderConfigurationError
        with patch('services.vlm_service._resolve_model_gateway_context', return_value=({}, 'codex_cli')), \
             patch('services.vlm_service.build_pdf_evidence', return_value=_evidence_manifest([
                 'Example paper text'])), \
             patch('services.vlm_service.call_text_model',
                   side_effect=ProviderConfigurationError('missing CLI')) as call:
            with self.assertRaises(ProviderConfigurationError):
                extract_knowledge_from_paper('dummy.pdf', 'PROMPT', '', 'codex-cli/gpt-6-astra')
        call.assert_called_once()

    def test_codex_page_failure_is_disclosed_before_model_calls(self):
        failed_manifest = build_evidence_manifest(
            paper_id="synthetic",
            source_file_hash=hashlib.sha256(b"failed-paper").hexdigest(),
            source_size_bytes=10,
            total_pages=2,
            pages=[
                {"page_number": 1, "status": "ok", "text": "usable"},
                {"page_number": 2, "status": "error", "error_type": "PdfReadError"},
            ],
        )
        with patch('services.vlm_service._resolve_model_gateway_context', return_value=({}, 'codex_cli')), \
             patch('services.vlm_service.build_pdf_evidence', return_value=failed_manifest), \
             patch('services.vlm_service.call_text_model') as call:
            with self.assertRaisesRegex(PaperExtractionError, "失败.*2|failed pages=2"):
                extract_knowledge_from_paper(
                    'dummy.pdf', 'PROMPT', '', 'codex-cli/gpt-6-astra'
                )
        call.assert_not_called()

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

        result = extract_knowledge_from_paper(
            pdf_filepath="dummy.pdf",
            prompt="PROMPT",
            api_key="test-key",
            model="gpt-5.4",
            fallback_text="Example Paper\nAlice\nBob",
        )
        parsed, raw, file_id, assistant_id, thread_id, vector_store_id = result

        self.assertEqual(parsed["title"], "Example Paper")
        self.assertEqual(raw, '{"title":"Example Paper"}')
        self.assertEqual(file_id, "file-123")
        self.assertEqual(thread_id, "resp-good")
        self.assertEqual(vector_store_id, "vs-1")
        self.assertEqual(assistant_id, "")
        self.assertEqual(result.coverage["model_input_coverage"], "provider_retrieval_unverified")
        self.assertTrue(result.coverage["local_text_fallback_used"])
        self.assertEqual(mock_run_once.call_count, 2)

        second_prompt = mock_run_once.call_args_list[1].args[3]
        self.assertIn("LOCAL_PDF_TEXT_BEGIN", second_prompt)
        self.assertIn("Example Paper\nAlice\nBob", second_prompt)
        mock_ensure_file.assert_called_once()

    @patch("services.vlm_service._resolve_image_paths_for_pages")
    @patch("services.vlm_service.build_pdf_evidence")
    @patch("services.vlm_service.call_text_model")
    @patch("services.vlm_service._resolve_model_gateway_context", return_value=({}, "codex_cli"))
    def test_extract_can_use_codex_cli_local_context(
        self,
        mock_gateway_context,
        mock_call_text_model,
        mock_build_pdf_evidence,
        mock_resolve_image_paths,
    ):
        image_path = Path(__file__).with_name("tmp-codex-paper.png")
        image_path.write_text("fake", encoding="utf-8")
        mock_build_pdf_evidence.return_value = _evidence_manifest([
            "Title: Codex Paper\nAuthors: Alice",
            "Method: Transformer\nDataset: ImageNet",
        ])
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
        self.assertIn("[HIERARCHICAL_EVIDENCE_NOTES]", final_call["user"])
        self.assertIn("[NOTE id=map:", final_call["user"])
        self.assertEqual(final_call["image_paths"], [str(image_path)])

    @patch("services.vlm_service._resolve_image_paths_for_pages", return_value=[])
    @patch("services.vlm_service.call_text_model")
    @patch("services.vlm_service._resolve_model_gateway_context", return_value=({}, "codex_cli"))
    def test_codex_cli_reads_late_pages_from_real_synthetic_pdf(
        self,
        mock_gateway_context,
        mock_call_text_model,
        mock_resolve_image_paths,
    ):
        pages = [
            ("evidence " * 500) + ("LATE_DISCOVERY" if number == 18 else "")
            for number in range(1, 19)
        ]
        map_prompts = []
        final_raw = (
            '{"title":"Long Paper","authors":["Alice"],"problem_area":"视觉",'
            '"abstract_summary":"摘要","techniques":[{"name":"Late Method","role":"主干",'
            '"aliases":[],"builds_on":[]}],"datasets":[],"baselines":[],"key_findings":[]}'
        )

        def respond(*args, **kwargs):
            user = kwargs["user"]
            if "[SOURCE_BATCH" in user:
                map_prompts.append(user)
                return "pages and evidence retained" + (" LATE_DISCOVERY" if "LATE_DISCOVERY" in user else "")
            if "[REDUCE " in user:
                return "reduced evidence LATE_DISCOVERY"
            return final_raw

        mock_call_text_model.side_effect = respond
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "long.pdf"
            pdf_path.write_bytes(_pdf_bytes(*pages))
            result = extract_knowledge_from_paper(
                pdf_filepath=str(pdf_path),
                prompt="PROMPT",
                api_key="",
                model="codex-cli/gpt-5.4",
                checkpoint_root=Path(directory) / "checkpoints",
            )

        self.assertEqual(result.parsed["title"], "Long Paper")
        self.assertGreater(len(map_prompts), 4)
        self.assertTrue(any("LATE_DISCOVERY" in prompt for prompt in map_prompts))
        self.assertEqual(result.coverage["source_pages"], 18)
        self.assertEqual(result.coverage["map_batches_completed"], len(map_prompts))
        self.assertEqual(result.coverage["model_input_coverage"], "all_extracted_text_chunks")

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
    @patch("services.vlm_service.build_pdf_evidence")
    @patch("services.vlm_service.call_text_model")
    @patch("services.vlm_service._resolve_model_gateway_context", return_value=({}, "codex_cli"))
    def test_extract_retries_with_lighter_context_after_codex_timeout(
        self,
        mock_gateway_context,
        mock_call_text_model,
        mock_build_pdf_evidence,
        mock_resolve_image_paths,
    ):
        image_paths = ["img1.png", "img2.png", "img3.png"]
        mock_build_pdf_evidence.return_value = _evidence_manifest([
            "Title: Codex Paper\nAuthors: Alice"
        ])
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
