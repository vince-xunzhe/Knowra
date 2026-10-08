import concurrent.futures
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.task_store import TaskStore, TaskConflict
from services import task_runtime
from services.task_executor import Context


class TaskQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = TaskStore(Path(self.tmp.name) / 'tasks.db')
        self.queue.initialize()

    def test_concurrent_submissions_are_idempotent(self):
        def submit(_):
            return self.queue.submit('papers', 'papers', {'paper_ids': ['p']})['job_id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(submit, range(16)))
        self.assertEqual(len(set(ids)), 1)
        self.assertIsNotNone(self.queue.claim())
        self.assertIsNone(self.queue.claim())

    def test_request_key_survives_completion(self):
        job = self.queue.submit('papers', 'papers', {}, 'key')
        self.assertEqual(self.queue.submit('papers', 'papers', {}, 'second-key')['job_id'], job['job_id'])
        self.queue.update(job['job_id'], status='completed')
        self.assertEqual(self.queue.submit('papers', 'papers', {}, 'key')['job_id'], job['job_id'])
        self.assertEqual(self.queue.submit('papers', 'papers', {}, 'second-key')['job_id'], job['job_id'])
        with self.assertRaises(TaskConflict):
            self.queue.submit('papers', 'papers', {'changed': True}, 'key')

    def test_pipeline_and_manual_tasks_cannot_overlap(self):
        self.queue.submit('pipeline', 'pipeline', {})
        with self.assertRaises(TaskConflict):
            self.queue.submit('papers', 'papers', {})

    def test_status_list_does_not_load_large_reports_or_checkpoints(self):
        job = self.queue.submit('lint', 'lint', {})
        self.queue.update(job['job_id'], result={'report': 'x' * 100000}, checkpoints={'private': 'large'})
        for item in [self.queue.recent()[0], self.queue.latest('lint', summary=True)]:
            self.assertIsNone(item['result'])
            self.assertIsNone(item['checkpoints'])
        self.assertEqual(len(self.queue.get(job['job_id'])['result']['report']), 100000)

    def test_resume_keeps_successful_checkpoints(self):
        job = self.queue.submit('papers', 'papers', {})
        ctx = Context(self.queue, job)
        ctx.step('paper:p', lambda: {'saved': True})
        self.queue.claim()
        self.queue.recover()
        recovered = self.queue.get(job['job_id'])
        self.assertEqual(recovered['status'], 'interrupted')
        resumed = self.queue.resume(job['job_id'])
        value = Context(self.queue, resumed).step('paper:p', lambda: self.fail('repeated completed work'))
        self.assertEqual(value, {'saved': True})

    def test_resume_conflicts_with_active_writer(self):
        job = self.queue.submit('papers', 'papers', {})
        self.queue.update(job['job_id'], status='failed')
        self.queue.submit('compile', 'compile', {})
        with self.assertRaises(TaskConflict):
            self.queue.resume(job['job_id'])

    def test_queued_snapshot_does_not_reuse_old_success(self):
        job = self.queue.submit('papers', 'papers', {'paper_ids': ['p']})
        self.queue.publish(job['job_id'], 'papers', {'running': False, 'done': 1})
        with patch.object(task_runtime, '_store', self.queue), patch.dict(os.environ, {'KNOWRA_TASK_EXECUTOR': '0'}):
            state = task_runtime.snapshot('papers', {'running': False})
        self.assertTrue(state['running'])
        self.assertEqual(state['done'], 0)

    def test_policy_adapts_to_local_and_external(self):
        with patch.dict(os.environ, {'KNOWRA_WORKER_MODE': 'auto'}), patch('config.is_cloud_mode', return_value=False):
            self.assertEqual(task_runtime.policy()['mode'], 'managed')
        with patch.dict(os.environ, {'KNOWRA_WORKER_MODE': 'external'}):
            self.assertEqual(task_runtime.policy()['mode'], 'external')
            with patch.object(task_runtime.subprocess, 'Popen') as spawn:
                task_runtime.ensure_worker()
                spawn.assert_not_called()

    def start_worker(self, code):
        env = dict(os.environ, KNOWRA_TASK_DB=str(self.queue.path), KNOWRA_TASK_EXECUTOR='1')
        child = subprocess.Popen([sys.executable, '-c', code], env=env,
                                 cwd=Path(__file__).resolve().parents[1],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def stop():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        self.addCleanup(stop)
        return child

    def test_supervisor_checks_worker_without_browser_polling(self):
        stopped = MagicMock()
        stopped.wait.side_effect = [False, True]
        with patch.object(task_runtime, '_supervisor', None), \
             patch.object(task_runtime, '_supervisor_stop', stopped), \
             patch.object(task_runtime, 'in_worker', return_value=False), \
             patch.object(task_runtime, 'policy', return_value={'mode': 'managed'}), \
             patch.object(task_runtime, 'ensure_worker') as ensure:
            task_runtime.start_supervisor()
            task_runtime._supervisor.join(2)
            task_runtime.stop_supervisor()
            self.assertEqual(ensure.call_count, 2)

    def wait_status(self, job, status):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            state = self.queue.get(job['job_id'])
            if state['status'] == status:
                return state
            time.sleep(.05)
        self.fail(f'Job did not reach {status}: {state}')

    def test_real_worker_isolated_and_readable_across_api_restart(self):
        job = self.queue.submit('test', 'papers', {})
        self.start_worker('''
import os,time
from services.task_executor import HANDLERS
def work(ctx,payload):
    end=time.monotonic()+1.5
    while time.monotonic()<end: sum(range(1000))
    return {'pid':os.getpid()}
HANDLERS['test']=work
from scripts.task_worker import main
main()
''')
        self.wait_status(job, 'running')
        # Reopening the durable store models a new HTTP process, not an in-memory cache.
        reopened = TaskStore(self.queue.path)
        start = time.monotonic()
        for _ in range(20):
            self.assertIsNotNone(reopened.get(job['job_id']))
        self.assertLess(time.monotonic() - start, 1)
        result = self.wait_status(job, 'completed')
        self.assertNotEqual(result['result']['pid'], os.getpid())

    def test_api_lifecycles_do_not_own_worker_lifetime(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers.jobs import router
        job = self.queue.submit('test', 'papers', {})
        child = self.start_worker('''
import time
from services.task_executor import HANDLERS
HANDLERS['test']=lambda ctx,payload: (time.sleep(1.5) or {'ok':True})
from scripts.task_worker import main
main()
''')
        self.wait_status(job, 'running')
        with patch.object(task_runtime, '_store', self.queue), patch.object(task_runtime, 'ensure_worker'):
            for _ in range(2):
                app = FastAPI()
                app.include_router(router)
                with TestClient(app) as client:
                    response = client.get('/api/jobs/' + job['job_id'])
                    self.assertEqual(response.status_code, 200)
                self.assertIsNone(child.poll())
        self.wait_status(job, 'completed')

    def test_killed_worker_is_interrupted_not_success_and_duplicate_worker_cannot_claim(self):
        job = self.queue.submit('test', 'papers', {})
        code = '''
import time
from services.task_executor import HANDLERS
def work(ctx,payload):
    ctx.step('saved', lambda: True)
    time.sleep(30)
HANDLERS['test']=work
from scripts.task_worker import main
main()
'''
        first = self.start_worker(code)
        self.wait_status(job, 'running')
        second = self.start_worker(code)
        self.assertEqual(second.wait(timeout=5), 0)
        first.kill()
        first.wait(timeout=5)
        self.start_worker(code)
        result = self.wait_status(job, 'interrupted')
        self.assertIsNone(result['result'])


if __name__ == '__main__':
    unittest.main()
