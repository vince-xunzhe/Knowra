import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.paper_evidence_service import build_evidence_manifest
from services.paper_extraction_hierarchy import (
    ExtractionCoverageError,
    build_source_batches,
    prepare_hierarchical_notes,
    provider_file_search_coverage,
)
from services.pipeline_manifest import (
    extraction_coverage_status,
    invalidate_from,
    record_layer,
)


def manifest_for(pages):
    source_hash = hashlib.sha256("hierarchy-fixture".encode()).hexdigest()
    return build_evidence_manifest(
        paper_id="fixture-paper",
        source_file_hash=source_hash,
        source_size_bytes=sum(len(str(item.get("text") or "")) for item in pages),
        total_pages=len(pages),
        pages=pages,
    )


class FullPaperHierarchyTests(unittest.TestCase):
    def test_long_paper_accounts_for_late_pages_without_four_batch_cap(self):
        pages = [
            {
                "page_number": number,
                "status": "ok",
                "text": (f"page-{number} " + ("evidence " * 900))
                + ("LATE_DISCOVERY" if number == 18 else ""),
            }
            for number in range(1, 19)
        ]
        manifest = manifest_for(pages)

        batches = build_source_batches(manifest)

        self.assertGreater(len(batches), 4)
        self.assertIn("LATE_DISCOVERY", batches[-1].text)
        planned = [evidence_id for batch in batches for evidence_id in batch.evidence_ids]
        self.assertEqual(planned, [chunk["id"] for chunk in manifest["chunks"]])
        self.assertIn(18, batches[-1].pages)

    def test_oversized_page_is_split_without_dropping_source_chunks(self):
        manifest = manifest_for([
            {"page_number": 1, "status": "ok", "text": "token " * 8_000}
        ])

        batches = build_source_batches(manifest)

        self.assertGreater(len(manifest["chunks"]), 1)
        self.assertEqual(
            [item for batch in batches for item in batch.evidence_ids],
            [chunk["id"] for chunk in manifest["chunks"]],
        )
        self.assertTrue(all(batch.pages == (1,) for batch in batches))

    def test_failed_page_stops_before_any_model_call(self):
        manifest = manifest_for([
            {"page_number": 1, "status": "ok", "text": "usable text"},
            {"page_number": 2, "status": "error", "error_type": "PdfReadError"},
        ])
        calls = []

        with self.assertRaisesRegex(ExtractionCoverageError, "failed pages=2"):
            prepare_hierarchical_notes(
                manifest,
                prompt="prompt",
                model="codex-cli/test",
                reasoning_effort=None,
                call_map=lambda batch: calls.append(batch) or "note",
                call_reduce=lambda *_: "reduced",
            )

        self.assertEqual(calls, [])

    def test_interrupted_run_resumes_completed_batches(self):
        manifest = manifest_for([
            {
                "page_number": number,
                "status": "ok",
                "text": f"page {number} " + ("source " * 1_500),
            }
            for number in range(1, 7)
        ])
        first_calls = []

        with tempfile.TemporaryDirectory() as directory:
            def interrupt(batch):
                first_calls.append(batch.batch_id)
                if len(first_calls) == 3:
                    raise RuntimeError("interrupted")
                return f"note for {batch.batch_id}"

            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                prepare_hierarchical_notes(
                    manifest,
                    prompt="prompt",
                    model="codex-cli/test",
                    reasoning_effort="medium",
                    call_map=interrupt,
                    call_reduce=lambda *_: "reduced",
                    checkpoint_root=Path(directory),
                )

            resumed_calls = []
            result = prepare_hierarchical_notes(
                manifest,
                prompt="prompt",
                model="codex-cli/test",
                reasoning_effort="medium",
                call_map=lambda batch: resumed_calls.append(batch.batch_id) or f"note for {batch.batch_id}",
                call_reduce=lambda *_: "reduced",
                checkpoint_root=Path(directory),
            )

            self.assertEqual(result.coverage["resumed_map_batches"], 2)
            self.assertNotIn(first_calls[0], resumed_calls)
            self.assertNotIn(first_calls[1], resumed_calls)
            result.checkpoint.save_final_response('{"title":"done"}')

            again = prepare_hierarchical_notes(
                manifest,
                prompt="prompt",
                model="codex-cli/test",
                reasoning_effort="medium",
                call_map=lambda _: self.fail("all map notes should resume"),
                call_reduce=lambda *_: self.fail("all reductions should resume"),
                checkpoint_root=Path(directory),
            )
            self.assertEqual(again.checkpoint.final_response(), '{"title":"done"}')

    def test_hierarchical_reduction_is_bounded_and_reports_coverage(self):
        manifest = manifest_for([
            {
                "page_number": number,
                "status": "ok",
                "text": f"page {number} " + ("source " * 1_500),
            }
            for number in range(1, 9)
        ])
        reductions = []

        result = prepare_hierarchical_notes(
            manifest,
            prompt="prompt",
            model="codex-cli/test",
            reasoning_effort=None,
            call_map=lambda batch: (
                f"pages={batch.pages}; evidence={batch.evidence_ids}\n" + ("detail " * 900)
            ),
            call_reduce=lambda level, group_id, text, pages: (
                reductions.append((level, group_id, pages)) or f"pages={pages}; reduced evidence"
            ),
            final_notes_characters=8_000,
        )

        self.assertTrue(reductions)
        self.assertEqual(result.coverage["model_input_coverage"], "all_extracted_text_chunks")
        self.assertEqual(result.coverage["map_batches_completed"], result.coverage["map_batches"])
        self.assertEqual(result.coverage["failed_pages"], 0)
        self.assertLessEqual(result.coverage["final_notes_characters"], 8_000)

    def test_provider_file_search_does_not_claim_page_retrieval(self):
        manifest = manifest_for([
            {"page_number": 1, "status": "ok", "text": "paper text"}
        ])

        coverage = provider_file_search_coverage(manifest)

        self.assertEqual(coverage["model_input_coverage"], "provider_retrieval_unverified")
        self.assertTrue(coverage["local_page_iteration_complete"])
        self.assertIsNone(coverage["page_iteration_complete"])

    def test_ui_coverage_summary_excludes_source_text_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            record_layer(
                "paper-1",
                "extraction_coverage",
                {
                    "strategy": "full-page-hierarchy-v1",
                    "source_sha256": "a" * 64,
                    "source_pages": 2,
                    "attempted_pages": 2,
                    "failed_pages": 1,
                    "page_sources": [
                        {"page_number": 1, "status": "ok", "secret": "source text"},
                        {"page_number": 2, "status": "error", "error_type": "PdfReadError"},
                    ],
                    "limitations": ["semantic omission remains possible"],
                },
                root=Path(directory),
            )

            summary = extraction_coverage_status("paper-1", root=Path(directory))

            invalidate_from("paper-1", "extraction", root=Path(directory))
            invalidated = extraction_coverage_status("paper-1", root=Path(directory))

        self.assertEqual(summary["failed_page_numbers"], [2])
        self.assertNotIn("source_sha256", summary)
        self.assertNotIn("page_sources", summary)
        self.assertNotIn("source text", str(summary))
        self.assertIsNone(invalidated)


if __name__ == "__main__":
    unittest.main()
