import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from routers.papers import get_paper_source_evidence
from services.paper_evidence_service import build_evidence_manifest
from services.paper_source_compilation import (
    compile_source_wiki,
    load_current_evidence_manifest,
    save_current_evidence_manifest,
    source_evidence_summary,
    source_page_payload,
)
from services.wiki_compiler import _parse_frontmatter, compile_paper_page


SOURCE_HASH = "a" * 64


def _manifest(*, pages: int = 6, characters_per_page: int = 3_200):
    records = []
    for page in range(1, pages + 1):
        body = (f"PAGE-{page}\n" + (f"page {page} method detail. " * 220))[
            : characters_per_page - 180
        ]
        if page == pages:
            body += (
                "\nTable 9: held-out results\n"
                "Model Split Metric\n"
                "FullPaperModel test 92.7\n"
                "TAIL-OF-PAPER-UNIQUE-FACT"
            )
        records.append({
            "page_number": page,
            "status": "ok",
            "text": body[:characters_per_page],
        })
    return build_evidence_manifest(
        paper_id="1",
        source_file_hash=SOURCE_HASH,
        source_size_bytes=sum(len(item["text"]) for item in records),
        total_pages=pages,
        pages=records,
        parser_version="synthetic-v1",
    )


class PaperSourceCompilationTests(unittest.TestCase):
    def test_full_source_batches_include_tail_table_and_resume(self):
        manifest = _manifest()
        prompts = []

        def compile_batch(batch, index, total):
            prompts.append(batch.text)
            return f"### Batch {index}/{total}\nCompiled pages {list(batch.pages)}"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = compile_source_wiki(
                manifest,
                model="codex/test",
                reasoning_effort="high",
                prompt_version="test-v1",
                call_batch=compile_batch,
                checkpoint_root=root,
                input_character_budget=4_000,
            )
            self.assertGreater(first.coverage["compile_batches"], 4)
            self.assertEqual(first.coverage["model_calls_this_attempt"], len(prompts))
            self.assertIn("TAIL-OF-PAPER-UNIQUE-FACT", "\n".join(prompts))
            self.assertIn("Model Split Metric", "\n".join(prompts))
            self.assertEqual(first.coverage["text_coverage"], "all_locally_extracted_chunks_presented")
            self.assertEqual(first.coverage["visual_coverage"], "unsupported")

            second = compile_source_wiki(
                manifest,
                model="codex/test",
                reasoning_effort="high",
                prompt_version="test-v1",
                call_batch=lambda *_: self.fail("checkpointed batches must be reused"),
                checkpoint_root=root,
                input_character_budget=4_000,
            )
            self.assertEqual(second.coverage["model_calls_this_attempt"], 0)
            self.assertEqual(
                second.coverage["resumed_batches"],
                first.coverage["compile_batches"],
            )

    def test_private_manifest_and_page_payload_are_source_bound(self):
        manifest = _manifest(pages=2)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library_hash = "c" * 32
            path = save_current_evidence_manifest(
                manifest,
                root=root,
                library_file_hash=library_hash,
            )
            self.assertTrue(path.is_file())
            self.assertNotIn("1.json", path.name)
            loaded = load_current_evidence_manifest(
                root=root,
                paper_id="1",
                library_file_hash=library_hash,
                source_sha256=SOURCE_HASH,
            )
            self.assertEqual(loaded, manifest)
            self.assertIsNone(load_current_evidence_manifest(
                root=root,
                paper_id="1",
                library_file_hash=library_hash,
                source_sha256="b" * 64,
            ))
            self.assertIsNone(load_current_evidence_manifest(
                root=root,
                paper_id="1",
                library_file_hash="d" * 32,
                source_sha256=SOURCE_HASH,
            ))
            page = source_page_payload(manifest, 2)
            self.assertEqual(page["page"]["page_number"], 2)
            self.assertTrue(page["page"]["chunks"])
            self.assertIn(
                "TAIL-OF-PAPER-UNIQUE-FACT",
                "".join(chunk["text"] for chunk in page["page"]["chunks"]),
            )
            summary = source_evidence_summary(manifest)
            self.assertEqual(summary["source_pages"], 2)
            self.assertEqual(summary["source_chunks"], manifest["coverage"]["chunk_count"])
            self.assertEqual(summary["visual_coverage"], "unsupported")

    def test_source_evidence_route_returns_only_requested_page(self):
        manifest = _manifest(pages=3)

        class FakeQuery:
            def filter(self, *_args):
                return self

            def first(self):
                return SimpleNamespace(id="1", file_hash=SOURCE_HASH)

        class FakeDb:
            def query(self, *_args):
                return FakeQuery()

        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            save_current_evidence_manifest(
                manifest,
                root=data_root / "paper_evidence",
                library_file_hash=SOURCE_HASH,
            )
            with patch("routers.papers.DATA_DIR", data_root):
                payload = get_paper_source_evidence("1", page=3, db=FakeDb())
            self.assertEqual(payload["page"]["page_number"], 3)
            self.assertTrue(payload["page"]["chunks"])
            self.assertTrue(all(
                item["page_number"] == 3 for item in payload["page"]["chunks"]
            ))

    def test_production_compiler_uses_all_source_batches_and_preserves_notes(self):
        manifest = _manifest()
        paper = SimpleNamespace(
            id=1,
            filename="public-paper.pdf",
            filepath="papers/public-paper.pdf",
            file_hash=SOURCE_HASH,
            title="Public Paper",
            authors=["A. Author"],
            processed=True,
            processed_at=None,
            extraction_model="codex/test",
            raw_llm_response=json.dumps({"title": "Public Paper"}),
            notes="line one  \n\nline two\n",
        )
        seen_prompts = []
        seen_operation_ids = []

        def fake_call(_client, _model, _system, user, **kwargs):
            seen_prompts.append(user)
            seen_operation_ids.append(kwargs.get("attempt_operation_id"))
            return "### Located section\nA source-backed section [p.1]"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("services.wiki_compiler.WIKI_PAPERS_DIR", root / "wiki"),
                patch("services.wiki_compiler.DATA_DIR", root / "data"),
                patch("services.wiki_compiler.load_current_evidence_manifest", return_value=manifest),
                patch("services.wiki_compiler.load_config", return_value={}),
                patch("services.wiki_compiler.task_reasoning_effort", return_value="high"),
                patch("services.wiki_compiler._call_llm", side_effect=fake_call),
            ):
                path = compile_paper_page(paper, api_key="", model="codex/test")
            self.assertIsNotNone(path)
            text = path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            self.assertEqual(meta["compiler_policy"], "full-source-wiki-v1")
            self.assertEqual(meta["source_pages"], 6)
            self.assertEqual(meta["source_chunks"], manifest["coverage"]["chunk_count"])
            self.assertEqual(meta["text_coverage"], "all_locally_extracted_chunks_presented")
            self.assertEqual(meta["visual_coverage"], "unsupported")
            self.assertIn("line one  \n\nline two\n", body)
            combined = "\n".join(seen_prompts)
            self.assertIn("TAIL-OF-PAPER-UNIQUE-FACT", combined)
            self.assertIn("Model Split Metric", combined)
            self.assertTrue(seen_operation_ids)
            self.assertTrue(all(
                str(operation_id).startswith("paper-wiki:")
                for operation_id in seen_operation_ids
            ))


if __name__ == "__main__":
    unittest.main()
