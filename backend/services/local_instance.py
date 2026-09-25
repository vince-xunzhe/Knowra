"""A process-lifetime lock for the desktop HTTP backend, not its workers."""
import os
from pathlib import Path


class LocalBackendLock:
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def acquire(self):
        if self.file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                "Another Knowra backend is already using this local data directory. "
                "Stop the existing backend before starting another port."
            ) from exc
        self.file = handle
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()

    def release(self):
        if self.file is None:
            return
        if os.name == "nt":
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
        self.file = None
        # Keep the inode: unlinking allows another process to lock a different file.
