"""Managed local worker, or an explicitly externally supervised worker."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from path_utils import DATA_DIR, PROJECT_ROOT
from services.task_store import TaskStore
from services.worker_revision import runtime_revision

_lock = threading.Lock()
_store = None
_child = None
_last_start = 0.0
_start_error = None
_supervisor_stop = threading.Event()
_supervisor = None


def in_worker():
    return os.environ.get('KNOWRA_TASK_EXECUTOR') == '1'


def policy():
    from config import is_cloud_mode
    configured = os.environ.get('KNOWRA_WORKER_MODE', 'auto').lower()
    if configured not in {'auto', 'managed', 'external'}:
        raise ValueError('KNOWRA_WORKER_MODE must be auto, managed or external')
    mode = ('external' if is_cloud_mode() else 'managed') if configured == 'auto' else configured
    return {'configured': configured, 'mode': mode, 'concurrency': 1,
            'reason': 'SQLite library: one heavy writer; model routing remains task-specific',
            'python': sys.executable}


def store():
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                candidate = TaskStore(Path(os.environ.get('KNOWRA_TASK_DB', str(DATA_DIR / 'tasks.db'))))
                candidate.initialize()
                _store = candidate
    return _store


def ensure_worker():
    global _child, _last_start, _start_error
    if in_worker() or policy()['mode'] == 'external':
        return
    queue = store()
    with _lock:
        queue.target_revision(runtime_revision())
        heartbeat = queue.worker()
        if heartbeat and time.time() - heartbeat['heartbeat'] < 10:
            return
        if _child is not None and _child.poll() is None:
            return
        if time.monotonic() - _last_start < 5:
            return
        _last_start = time.monotonic()
        logs = DATA_DIR / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, KNOWRA_TASK_EXECUTOR='1', KNOWRA_TASK_DB=str(queue.path),
                   KNOWRA_WORKER_MANAGED='1')
        try:
            with (logs / 'task-worker.log').open('ab') as output:
                _child = subprocess.Popen(
                    [sys.executable, str(PROJECT_ROOT / 'backend/scripts/task_worker.py')],
                    cwd=str(PROJECT_ROOT / 'backend'), env=env, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=output, start_new_session=True, close_fds=True,
                )
            _start_error = None
        except OSError as exc:
            # The submission is already durable. Do not tell the client it
            # was rejected and invite a duplicate POST after a spawn failure.
            _start_error = str(exc)


def health():
    heartbeat = store().worker()
    online = bool(heartbeat and time.time() - heartbeat['heartbeat'] < 15)
    revision = heartbeat.get('revision') if heartbeat else None
    return {'online': bool(heartbeat and time.time() - heartbeat['heartbeat'] < 15),
            'heartbeat': heartbeat, 'start_error': _start_error,
            'legacy_worker': bool(online and not revision),
            'restart_pending': bool(online and revision and revision != store().target_revision()
                                    and policy()['mode'] == 'managed')}


def start_supervisor():
    global _supervisor
    if in_worker() or policy()['mode'] == 'external':
        return
    if _supervisor is not None and _supervisor.is_alive():
        return
    _supervisor_stop.clear()
    ensure_worker()
    def supervise():
        while not _supervisor_stop.wait(5):
            try:
                ensure_worker()
            except Exception:
                import traceback
                traceback.print_exc()
    _supervisor = threading.Thread(target=supervise, name='library-worker-supervisor', daemon=True)
    _supervisor.start()


def stop_supervisor():
    _supervisor_stop.set()
    if _supervisor is not None:
        _supervisor.join(timeout=6)
    # The detached worker deliberately survives the API lifecycle.


def submit(kind, channel, payload, request_key=None):
    job = store().submit(kind, channel, payload, request_key)
    ensure_worker()
    return job


def snapshot(channel, fallback):
    if in_worker():
        return dict(fallback)
    queue = store()
    state = queue.snapshot(channel)
    latest = queue.latest(channel, summary=True)
    if latest and latest['status'] == 'queued':
        return dict(fallback, running=True, job_id=latest['job_id'], job_status='queued',
                    total=len(latest['payload'].get('paper_ids', [])), done=0, errors=0,
                    current='排队等待独立 worker', phase='queued', failed_papers=[],
                    batch_error=None, last_error=None)
    if latest and latest['status'] in ('queued', 'running') and (not state or state.get('job_id') != latest['job_id']):
        state = dict(fallback, running=True, job_id=latest['job_id'], job_status=latest['status'],
                     total=0, done=0, errors=0, current='排队等待独立 worker')
    return state or dict(fallback)
