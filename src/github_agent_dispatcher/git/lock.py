from __future__ import annotations

import logging
import os
import time
from pathlib import Path


class RepoLockedError(Exception):
    pass


class RepoLock:
    """Cross-platform lock for a repository checkout.

    Implemented with an atomic ``O_CREAT | O_EXCL`` lock file (works on every
    filesystem) plus a stale-lock guard based on the recorded PID.  Explicitly
    avoids ``fcntl`` so it behaves identically on macOS and Windows.
    """

    def __init__(self, lock_path: Path, stale_after_seconds: int = 86400):
        self.lock_path = lock_path
        self.stale_after_seconds = stale_after_seconds
        self._held = False

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def acquire(self, timeout: float | None = None, poll_interval: float = 2.0) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._try_acquire():
                self._held = True
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            remaining = poll_interval
            if deadline is not None:
                remaining = min(remaining, max(0.0, deadline - time.monotonic()))
            time.sleep(remaining)

    def _try_acquire(self) -> bool:
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            self._cleanup_stale()
            return False
        except OSError:
            return False
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        return True

    def _cleanup_stale(self) -> None:
        try:
            raw = self.lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return
        pid = int(raw) if raw.isdigit() else None
        if pid is not None and self._pid_exists(pid):
            return
        logger = logging.getLogger(__name__)
        logger.warning("[lock] removing stale lock %s (owner pid=%s not alive)", self.lock_path, pid)
        try:
            self.lock_path.unlink()
        except OSError:
            pass

    def release(self) -> None:
        if not self._held:
            return
        try:
            self.lock_path.unlink()
        except OSError:
            pass
        self._held = False

    def __enter__(self) -> RepoLock:
        self.acquire(timeout=0)
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
