import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from database import get_db
from routers import papers, jobs
from services import task_runtime
from services.task_store import TaskStore


class PaperBackgroundDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = TaskStore(Path(self.tmp.name) / 'tasks.db')
        self.queue.initialize()
        for mock in [patch.object(task_runtime, '_store', self.queue),
                     patch.object(task_runtime, 'ensure_worker'),
                     patch.object(task_runtime, 'in_worker', return_value=False)]:
            mock.start()
            self.addCleanup(mock.stop)

    def test_submission_and_client_shutdown_never_run_model_in_api(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [SimpleNamespace(id='p1')]
        app = FastAPI()
        app.include_router(papers.router)
        app.include_router(jobs.router)
        app.dependency_overrides[get_db] = lambda: db
        with patch.object(papers, '_process_single', side_effect=AssertionError('model in API')) as run:
            started = time.monotonic()
            with TestClient(app) as client:
                first = client.post('/api/process')
                second = client.post('/api/process')
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(first.status_code, 200)
            self.assertTrue(first.json()['running'])
            self.assertEqual(first.json()['job_id'], second.json()['job_id'])
            run.assert_not_called()
            db.close.assert_called()
        self.assertEqual(len(self.queue.active()), 1)

    def test_reprocess_does_not_delete_existing_results_before_worker_claim(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(id='p', filename='p.pdf')
        with patch.object(papers, '_prepare_reprocess') as prepare:
            papers.reprocess_paper('p', db)
            prepare.assert_not_called()
        self.assertTrue(self.queue.latest('papers')['payload']['force'])

    def test_empty_submission_never_queues_work(self):
        papers._start_processing_worker([])
        self.assertEqual(self.queue.active(), [])
