import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.concept_source_compilation import (
    ConceptSourceDocument,
    build_source_batches,
    build_source_chunks,
    compile_bounded_concept,
)
from services.wiki_compiler import (
    _concept_source_documents,
    _parse_frontmatter,
    compile_concept_page,
)


class _FakeQuery:
    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return [("paper-1",)]


class _FakeDB:
    def query(self, *args, **kwargs):
        return _FakeQuery()


class ConceptSourceCompilationTests(unittest.TestCase):
    def test_wiki_source_documents_keep_complete_selected_fields(self):
        intro = "intro-" + "I" * 1800 + "INTRO_TAIL"
        method = "method-" + "M" * 2800 + "METHOD_TAIL"
        findings = ["finding-" + "F" * 700 + "FINDING_TAIL"]
        node = SimpleNamespace(id="concept-1", content=intro)
        paper = SimpleNamespace(
            id="paper-1",
            title="Long paper",
            filename="paper.pdf",
            raw_llm_response=json.dumps({
                "method": method,
                "key_findings": findings,
            }),
        )

        documents = _concept_source_documents(node, [paper])
        by_label = {document.label: document.text for document in documents}

        self.assertEqual(by_label["concept_description"], intro)
        self.assertEqual(by_label["method"], method)
        self.assertIn("FINDING_TAIL", by_label["key_findings"])

    def test_compile_page_records_coverage_and_routes_every_tail(self):
        tail = "TAIL_FROM_LATE_FIELD"
        paper = SimpleNamespace(
            id="paper-1",
            title="Long paper",
            filename="paper.pdf",
            legacy_id=None,
            processed=True,
            processed_at=None,
            extraction_model="synthetic",
            raw_llm_response=json.dumps({
                "method": "M" * 18_000,
                "limitations": "L" * 2_500 + tail,
            }),
        )
        node = SimpleNamespace(
            id="concept-1",
            title="Bounded concept",
            content="C" * 2_500 + "CONCEPT_TAIL",
            node_type="method",
            legacy_id=None,
            tags=[],
            source_paper_ids=["paper-1"],
        )
        prompts = []

        def fake_call(*args, **kwargs):
            prompts.append(args[3])
            if "/SOURCE]" in args[3]:
                return "## partial\n[[paper:paper-1]]"
            return "## merged\n[[paper:paper-1]]"

        with tempfile.TemporaryDirectory() as tmp, patch(
            "services.wiki_compiler.WIKI_CONCEPTS_DIR", Path(tmp)
        ), patch(
            "services.wiki_compiler.is_publishable_concept_node", return_value=True
        ), patch(
            "services.wiki_compiler._published_concept_source_papers", return_value=[paper]
        ), patch(
            "services.wiki_compiler.finish_read_snapshot"
        ), patch(
            "services.wiki_compiler.node_origin", return_value="promoted"
        ), patch(
            "services.wiki_compiler.load_config", return_value={}
        ), patch(
            "services.wiki_compiler.task_reasoning_effort", return_value=None
        ), patch(
            "services.wiki_compiler._call_llm", side_effect=fake_call
        ):
            path = compile_concept_page(node, _FakeDB(), "", "synthetic/model")
            self.assertIsNotNone(path)
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))

        source_prompts = "\n".join(prompt for prompt in prompts if "/SOURCE]" in prompt)
        self.assertIn(tail, source_prompts)
        self.assertIn("CONCEPT_TAIL", source_prompts)
        self.assertGreater(meta["source_compile_batches"], 1)
        self.assertTrue(all(len(prompt) <= meta["source_prompt_character_limit"] for prompt in prompts))
        self.assertEqual(meta["text_coverage"], "complete")
        self.assertEqual(meta["semantic_completeness"], "not_guaranteed")
        self.assertEqual(meta["visual_coverage"], "unsupported")
        self.assertIn("[[paper:paper-1]]", body)

    def test_source_chunks_preserve_long_intro_and_field_tails(self):
        intro = "I" * 1500 + "INTRO_TAIL"
        field = "F" * 2500 + "FIELD_TAIL"
        documents = [
            ConceptSourceDocument("concept:1:content", "concept_description", intro),
            ConceptSourceDocument("paper:1:method", "method", field, paper_id="1"),
        ]

        chunks = build_source_chunks(documents, chunk_characters=600)
        restored = {
            document.source_key: "".join(
                chunk.text for chunk in chunks if chunk.source_key == document.source_key
            )
            for document in documents
        }

        self.assertEqual(restored["concept:1:content"], intro)
        self.assertEqual(restored["paper:1:method"], field)
        self.assertTrue(restored["concept:1:content"].endswith("INTRO_TAIL"))
        self.assertTrue(restored["paper:1:method"].endswith("FIELD_TAIL"))

    def test_batches_are_bounded_and_round_robin_multiple_papers(self):
        documents = [
            ConceptSourceDocument("paper:1:method", "method", "A" * 1800, paper_id="1"),
            ConceptSourceDocument("paper:2:method", "method", "B" * 1800, paper_id="2"),
            ConceptSourceDocument("paper:3:method", "method", "C" * 1800, paper_id="3"),
        ]
        chunks = build_source_chunks(documents, chunk_characters=300)
        batches = build_source_batches(
            title="bounded concept",
            node_type="method",
            chunks=chunks,
            prompt_character_limit=1800,
        )

        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(batch.prompt) <= 1800 for batch in batches))
        self.assertEqual(
            {chunk.paper_id for chunk in batches[0].chunks},
            {"1", "2", "3"},
        )
        self.assertEqual(
            {chunk.chunk_id for batch in batches for chunk in batch.chunks},
            {chunk.chunk_id for chunk in chunks},
        )

    def test_round_robin_groups_multiple_fields_by_paper(self):
        documents = [
            ConceptSourceDocument("concept:1:content", "description", "D" * 500),
            ConceptSourceDocument("paper:1:method", "method", "A" * 500, paper_id="1"),
            ConceptSourceDocument("paper:1:limits", "limitations", "B" * 500, paper_id="1"),
            ConceptSourceDocument("paper:2:method", "method", "C" * 500, paper_id="2"),
            ConceptSourceDocument("paper:2:limits", "limitations", "E" * 500, paper_id="2"),
        ]
        chunks = build_source_chunks(documents, chunk_characters=300)
        batches = build_source_batches(
            title="bounded concept",
            node_type="method",
            chunks=chunks,
            prompt_character_limit=14_000,
        )

        order = [chunk.paper_id for chunk in batches[0].chunks]
        self.assertEqual(order[:6], [None, "1", "2", None, "1", "2"])
        self.assertEqual(order[6:], ["1", "2", "1", "2"])

    def test_compile_uses_every_map_batch_then_bounded_reduce(self):
        documents = [
            ConceptSourceDocument("concept:1:content", "description", "D" * 900),
            ConceptSourceDocument("paper:1:method", "method", "M" * 1600, paper_id="1"),
            ConceptSourceDocument("paper:2:findings", "key_findings", "K" * 1600, paper_id="2"),
        ]
        map_calls = []
        reduce_calls = []

        def call_map(batch, index, total):
            map_calls.append((batch, index, total))
            paper_ids = sorted({chunk.paper_id for chunk in batch.chunks if chunk.paper_id})
            return "## partial\n" + " ".join(f"[[paper:{paper_id}]]" for paper_id in paper_ids)

        def call_reduce(prompt, index, total, final):
            reduce_calls.append((prompt, index, total, final))
            return "## merged\n[[paper:1]] [[paper:2]]"

        compiled = compile_bounded_concept(
            title="bounded concept",
            node_type="method",
            documents=documents,
            call_map=call_map,
            call_reduce=call_reduce,
            chunk_characters=300,
            prompt_character_limit=1800,
        )

        self.assertGreater(len(map_calls), 1)
        self.assertGreaterEqual(len(reduce_calls), 1)
        self.assertTrue(all(len(call[0].prompt) <= 1800 for call in map_calls))
        self.assertTrue(all(len(call[0]) <= 1800 for call in reduce_calls))
        self.assertEqual(compiled.coverage["text_coverage"], "complete")
        self.assertEqual(compiled.coverage["semantic_completeness"], "not_guaranteed")
        self.assertEqual(compiled.coverage["source_papers"], 2)
        self.assertEqual(
            compiled.coverage["map_calls"] + compiled.coverage["reduce_calls"],
            len(map_calls) + len(reduce_calls),
        )
        self.assertIn("[[paper:1]]", compiled.body)
        self.assertIn("[[paper:2]]", compiled.body)


if __name__ == "__main__":
    unittest.main()
