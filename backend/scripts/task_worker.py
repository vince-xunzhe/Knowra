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


def main():
    queue = store()
    lock = LocalBackendLock(queue.path.with_suffix('.worker.lock'))
    try:
        lock.acquire()
    except RuntimeError:
        return
    try:
        queue.recover()
        if hasattr(os, 'nice'):
            os.nice(5)
        queue.heartbeat(os.getpid())
        # Heartbeats have their own tiny database; library/model work never
        # holds this connection. SIGKILL releases the lifetime lock via the OS.
        def heartbeat():
            while True:
                try:
                    queue.heartbeat(os.getpid())
                except Exception:
                    import traceback
                    traceback.print_exc()
                time.sleep(2)
        threading.Thread(target=heartbeat, daemon=True).start()
        from services.task_executor import execute
        while True:
            job = queue.claim()
            if job:
                execute(queue, job)
            else:
                time.sleep(0.5)
    finally:
        lock.release()


if __name__ == '__main__':
    main()
