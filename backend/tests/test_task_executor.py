import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, Paper
from services.task_store import TaskStore
from services import task_executor as executor
from routers import papers


class TaskExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.queue = TaskStore(root / 'tasks.db')
        self.queue.initialize()
        engine = create_engine(f'sqlite:///{root / "library.db"}')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine)
        for mock in [patch('database.SessionLocal', self.sessions),
                     patch.dict(os.environ, {'KNOWRA_TASK_EXECUTOR': '1'})]:
            mock.start()
            self.addCleanup(mock.stop)
        with self.sessions() as db:
            db.add_all([Paper(id=i, filepath=i+'.pdf', filename=i+'.pdf', file_hash=i) for i in ['a', 'b']])
            db.commit()
        previous = dict(papers.processing_state)
        self.addCleanup(papers.processing_state.update, previous)

    def test_resume_processes_only_unfinished_papers(self):
        calls = []
        fail_b = True
        def process(pid):
            calls.append(pid)
            if pid == 'b' and fail_b:
                papers.processing_state['done'] += 1
                papers.processing_state['errors'] += 1
                papers.processing_state['failed_papers'].append({'id': pid, 'reason': 'offline'})
                return
            with self.sessions() as db:
                db.get(Paper, pid).processed = True
                db.commit()
            papers.processing_state['done'] += 1
            papers.processing_state['succeeded'] += 1
        job = self.queue.submit('papers', 'papers', {'paper_ids': ['a', 'b']})
        with patch.object(papers, '_process_single', side_effect=process):
            executor.execute(self.queue, self.queue.claim())
            self.assertEqual(self.queue.get(job['job_id'])['status'], 'failed')
            self.assertIn('paper:a', self.queue.get(job['job_id'])['checkpoints'])
            fail_b = False
            self.queue.resume(job['job_id'])
            executor.execute(self.queue, self.queue.claim())
        self.assertEqual(calls, ['a', 'b', 'b'])
        self.assertEqual(self.queue.get(job['job_id'])['status'], 'completed')
        self.assertEqual(self.queue.snapshot('papers')['done'], 2)

    def test_pipeline_resumes_after_successful_stages_without_browser(self):
        job = self.queue.submit('pipeline', 'pipeline', {})
        with patch.object(papers, 'scan_papers', return_value={'pending': 2}) as scan, \
             patch.object(papers, 'paper_work_counts', return_value={'failed_count': 0}), \
             patch.object(executor, 'process_papers', return_value={}) as extract, \
             patch.object(executor, 'promote', side_effect=[RuntimeError('offline'), {}]) as promote, \
             patch('routers.promotion.accept_llm_proposals', return_value={}), \
             patch.object(executor, 'compile_items', return_value={}) as compile_pages, \
             patch.object(executor, 'lint', return_value={}) as lint:
            executor.execute(self.queue, self.queue.claim())
            self.assertEqual(self.queue.get(job['job_id'])['status'], 'failed')
            self.queue.resume(job['job_id'])
            executor.execute(self.queue, self.queue.claim())
            self.assertEqual(self.queue.get(job['job_id'])['status'], 'completed')
            scan.assert_called_once()
            extract.assert_called_once()
            self.assertEqual(promote.call_count, 2)
            compile_pages.assert_called_once()
            lint.assert_called_once()

    def test_lint_agent_failure_retains_rule_report(self):
        report = {'judgment': {'error': 'provider offline'}, 'counts': {'concepts_scanned': 2}}
        job = self.queue.submit('lint', 'lint', {'use_llm': True})
        with patch('services.wiki_lint_service.run_lint', return_value=report):
            executor.execute(self.queue, self.queue.claim())
        saved = self.queue.get(job['job_id'])
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['result'], report)

    def test_compilation_restores_successful_page_checkpoints(self):
        from routers import wiki
        with self.sessions() as db:
            for paper in db.query(Paper).all():
                paper.processed = True
                paper.raw_llm_response = '{"title":"Paper"}'
            db.commit()
        calls = []
        fail_b = True
        def compile_page(paper, *args):
            calls.append(paper.id)
            if paper.id == 'b' and fail_b:
                raise RuntimeError('provider unavailable')
            return Path('/tmp') / (paper.id + '.md')
        job = self.queue.submit('compile', 'compile', {'paper_ids': ['a', 'b']})
        with patch.object(wiki, 'compile_paper_page', side_effect=compile_page), \
             patch.object(wiki, 'compute_freshness_summary', return_value={}), \
             patch.object(wiki, 'reconcile_paper_pages_dir', return_value={}), \
             patch.object(wiki, 'reconcile_concept_pages_dir', return_value={}), \
             patch.object(wiki.wiki_index, 'refresh_index'), \
             patch.object(wiki.wiki_search_service, 'rebuild_index'), \
             patch('config.load_config', return_value={}), \
             patch('config.task_model_id', return_value='test'):
            executor.execute(self.queue, self.queue.claim())
            self.assertEqual(self.queue.get(job['job_id'])['status'], 'failed')
            fail_b = False
            self.queue.resume(job['job_id'])
            executor.execute(self.queue, self.queue.claim())
        self.assertEqual(calls, ['a', 'b', 'b'])
        result = self.queue.get(job['job_id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result']['compiled']['papers'], 2)
