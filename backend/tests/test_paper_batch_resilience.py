import unittest
from unittest.mock import patch

from routers import papers


class PaperBatchResilienceTests(unittest.TestCase):
    def setUp(self):
        papers.processing_state.update(
            {
                "running": False,
                "total": 0,
                "done": 0,
                "errors": 0,
                "current": "",
                "succeeded": 0,
                "failed_papers": [],
                "max_retries": 0,
                "batch_error": None,
                "last_message": "",
                "started_at": None,
                "finished_at": None,
            }
        )

    def test_record_sync_failure_does_not_escape_processing(self):
        paper = type("PaperStub", (), {"id": "paper-1", "filename": "paper.pdf"})()
        with (
            patch.object(papers, "sync_record_from_paper", side_effect=OSError("read-only")),
            patch.object(papers.logger, "exception") as log_exception,
        ):
            papers._sync_processing_record(paper, "process")

        log_exception.assert_called_once()

    def test_unhandled_item_failure_does_not_abort_later_papers(self):
        visited = []

        def process_single(paper_id):
            visited.append(paper_id)
            if paper_id == "paper-1":
                raise RuntimeError("synthetic entry failure")
            papers.processing_state["done"] += 1
            papers.processing_state["succeeded"] += 1

        def record_failure(paper_id, exc):
            papers.processing_state["done"] += 1
            papers.processing_state["errors"] += 1
            papers.processing_state["batch_error"] = str(exc)
            papers.processing_state["failed_papers"].append(
                {"id": paper_id, "filename": "first.pdf", "reason": str(exc)}
            )

        with (
            patch.object(papers, "_process_single", side_effect=process_single),
            patch.object(
                papers,
                "_record_unhandled_processing_failure",
                side_effect=record_failure,
            ),
        ):
            papers._process_many_background(["paper-1", "paper-2"])

        self.assertEqual(visited, ["paper-1", "paper-2"])
        self.assertFalse(papers.processing_state["running"])
        self.assertEqual(papers.processing_state["done"], 2)
        self.assertEqual(papers.processing_state["succeeded"], 1)
        self.assertEqual(papers.processing_state["errors"], 1)
        self.assertIn("1 篇成功", papers.processing_state["last_message"])
        self.assertIn("1 篇失败", papers.processing_state["last_message"])


if __name__ == "__main__":
    unittest.main()
