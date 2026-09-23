import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import wiki
from services.wiki_lint_job import WikiLintJob


class WikiLintJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "job.json"
        self.job = WikiLintJob(self.path)

    def wait_done(self):
        deadline = time.monotonic() + 3
        while self.job.snapshot()["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotEqual(self.job.snapshot()["status"], "running")

    def test_submit_returns_while_worker_is_blocked_and_duplicate_start_reuses_job(self):
        entered = threading.Event()
        release = threading.Event()
        db_factory = MagicMock()

        def lint(db, *, use_llm, on_progress):
            on_progress("Agent 正在判定检查结果")
            entered.set()
            release.wait(3)
            return {"judgment": {}, "counts": {"concepts_scanned": 2}}

        app = FastAPI()
        app.include_router(wiki.router)
        with patch.object(wiki, "lint_job", self.job), \
                patch("database.SessionLocal", db_factory), \
                patch("services.wiki_lint_service.run_lint", side_effect=lint) as runner:
            client = TestClient(app)
            try:
                response = client.post("/api/wiki/lint/run", json={"use_llm": True})
                self.assertEqual(response.status_code, 202)
                self.assertTrue(entered.wait(2))
                self.assertEqual(client.get("/api/wiki/lint/job").json()["status"], "running")
                repeated = client.post("/api/wiki/lint/run", json={"use_llm": False})
                self.assertEqual(response.json()["job_id"], repeated.json()["job_id"])
                self.assertEqual(runner.call_count, 1)
            finally:
                release.set()
                self.wait_done()
            self.assertEqual(client.get("/api/wiki/lint/job").json()["status"], "completed")
            self.assertEqual(client.get("/api/wiki/lint/result").json()["result"]["counts"]["concepts_scanned"], 2)
            db_factory.return_value.__exit__.assert_called_once()
        restored = WikiLintJob(self.path)
        self.assertEqual(restored.report(), self.job.report())
        self.assertEqual(restored.snapshot()["status"], "completed")

    def test_agent_failure_keeps_rule_report_as_warning(self):
        result = {"judgment": {"used_model": False, "error": "model timeout"}, "stubs": []}
        with patch("database.SessionLocal"), patch("services.wiki_lint_service.run_lint", return_value=result):
            self.job.start()
            self.wait_done()
        self.assertEqual(self.job.snapshot()["status"], "warning")
        self.assertEqual(self.job.report()["result"], result)

    def test_worker_failure_closes_session_and_allows_retry(self):
        with patch("database.SessionLocal") as db_factory, \
                patch("services.wiki_lint_service.run_lint", side_effect=RuntimeError("scan failed")):
            first = self.job.start()
            self.wait_done()
            db_factory.return_value.__exit__.assert_called_once()
        self.assertEqual(self.job.snapshot()["error"], "scan failed")
        with patch("database.SessionLocal"), patch("services.wiki_lint_service.run_lint", return_value={"judgment": {}}):
            second = self.job.start(False)
            self.wait_done()
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertEqual(self.job.snapshot()["status"], "completed")

    def test_restart_reports_interruption_instead_of_running_forever(self):
        with patch("services.wiki_lint_job.threading.Thread"):
            self.job.start()
        restored = WikiLintJob(self.path)
        self.assertEqual(restored.snapshot()["status"], "failed")
        self.assertIn("重启", restored.snapshot()["error"])

    def test_thread_start_failure_does_not_leave_job_running(self):
        with patch("services.wiki_lint_job.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("cannot start")
            with self.assertRaises(RuntimeError):
                self.job.start()
        self.assertEqual(self.job.snapshot()["status"], "idle")


if __name__ == "__main__":
    unittest.main()
