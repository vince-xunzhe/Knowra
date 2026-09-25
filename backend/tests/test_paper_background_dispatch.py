import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from database import get_db
from routers import papers


class PaperBackgroundDispatchTests(unittest.TestCase):
    def setUp(self):
        self.previous = dict(papers.processing_state)
        papers.processing_state["running"] = False
        self.addCleanup(papers.processing_state.update, self.previous)

    def test_response_and_client_shutdown_do_not_wait_for_model(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [SimpleNamespace(id="p1")]

        def worker(ids):
            try:
                self.assertEqual(ids, ["p1"])
                db.close.assert_called()
                entered.set()
                release.wait(5)
            finally:
                finished.set()

        app = FastAPI()
        app.include_router(papers.router)
        app.dependency_overrides[get_db] = lambda: db
        with patch.object(papers, "_process_many_background", side_effect=worker) as run:
            try:
                started = time.monotonic()
                with TestClient(app) as client:
                    first = client.post("/api/process")
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(finished.is_set())
                    second = client.post("/api/process")
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(second.json()["message"], "Processing already running")
                    self.assertEqual(run.call_count, 1)
                self.assertLess(time.monotonic() - started, 2)
            finally:
                release.set()
                self.assertTrue(finished.wait(2))

    def test_dispatch_failure_clears_running_state(self):
        with patch.object(papers.threading, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread unavailable")
            with self.assertRaises(HTTPException) as exc:
                papers._start_processing_worker(["p1"])
        self.assertEqual(exc.exception.status_code, 503)
        self.assertFalse(papers.processing_state["running"])
        self.assertIn("thread unavailable", papers.processing_state["batch_error"])

    def test_blocked_paper_worker_does_not_prevent_process_exit(self):
        code = '''
import threading
from routers import papers
entered = threading.Event()
def blocked(ids):
    entered.set()
    threading.Event().wait()
papers._process_many_background = blocked
papers._start_processing_worker(["test"])
assert entered.wait(1)
'''
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
