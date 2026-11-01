"""Run one isolated, persistent library worker: python scripts/task_worker.py."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KNOWRA_TASK_EXECUTOR'] = '1'

from services.local_instance import LocalBackendLock
from services.task_runtime import store
from services.worker_revision import runtime_revision


def reconcile_ask_generation_refresh(*, live_data_root=None):
    """Requeue a durable refresh marker even without an API supervisor."""
    from path_utils import DATA_DIR
    from services.ask_generation_refresh import (
        REFRESH_REQUEST_FILENAME,
        default_refresh_paths,
        ensure_pending_refresh,
    )

    root = DATA_DIR if live_data_root is None else Path(live_data_root)
    marker = default_refresh_paths(root)["state_root"] / REFRESH_REQUEST_FILENAME
    if marker.is_file():
        return ensure_pending_refresh(
            live_data_root=root,
            retry_after_seconds=60.0,
        )
    return {"requested": False, "reason": "marker_absent"}


def main():
    queue = store()
    revision = runtime_revision()
    managed = os.environ.get('KNOWRA_WORKER_MANAGED') == '1'
    stopped = threading.Event()
    heartbeat_thread = None
    lock = LocalBackendLock(queue.path.with_suffix('.worker.lock'))
    try:
        lock.acquire()
    except RuntimeError:
        return
    try:
        queue.recover()
        if hasattr(os, 'nice'):
            os.nice(5)
        queue.heartbeat(os.getpid(), revision)
        # Heartbeats have their own tiny database; library/model work never
        # holds this connection. SIGKILL releases the lifetime lock via the OS.
        def heartbeat():
            while not stopped.wait(2):
                try:
                    queue.heartbeat(os.getpid(), revision)
                except Exception:
                    import traceback
                    traceback.print_exc()
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        from services.task_executor import execute
        next_refresh_reconcile = 0.0
        while True:
            if managed and queue.target_revision() not in (None, revision):
                break
            job = queue.claim(revision if managed else None)
            if job:
                execute(queue, job)
            else:
                now = time.monotonic()
                if now >= next_refresh_reconcile:
                    next_refresh_reconcile = now + 5.0
                    try:
                        reconcile_ask_generation_refresh()
                    except Exception:
                        import traceback
                        traceback.print_exc()
                time.sleep(0.5)
    finally:
        stopped.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join()
        queue.clear_worker(os.getpid())
        lock.release()


if __name__ == '__main__':
    main()
