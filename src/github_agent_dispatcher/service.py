from __future__ import annotations

import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from github_agent_dispatcher.agents.base import AgentBackend
from github_agent_dispatcher.config import AppConfig
from github_agent_dispatcher.github.client import GitHubClient
from github_agent_dispatcher.jobs.queue import JobQueue
from github_agent_dispatcher.jobs.worker import JobWorker
from github_agent_dispatcher.polling.scanner import Scanner
from github_agent_dispatcher.storage.database import Database

logger = logging.getLogger(__name__)


class Service:
    def __init__(
        self,
        config: AppConfig,
        db: Database,
        github: GitHubClient,
        backend: AgentBackend,
    ):
        self.config = config
        self.db = db
        self.github = github
        self.queue = JobQueue(db)
        self.scanner = Scanner(config, github, self.queue)
        self.worker = JobWorker(config, self.queue, github, backend)
        self._stop = threading.Event()
        self._scan_lock = threading.Lock()

    # ------------------------------------------------------------------ scan
    def scan_now(self) -> dict:
        with self._scan_lock:
            result = self.scanner.scan()
        self.db.set_meta("last_scan_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.db.set_meta("last_scan_result", repr(result))
        return result

    # ------------------------------------------------------------- execution
    def drain_queue(self, max_jobs: int = 5) -> int:
        processed = 0
        for _ in range(max_jobs):
            job = self.queue.next_job()
            if job is None:
                break
            self.worker.try_process(job)
            processed += 1
        return processed

    def run(self, poll_interval: int | None = None) -> None:
        interval = poll_interval or self.config.poll_interval_seconds
        interrupted = self.db.reset_interrupted()
        if interrupted:
            logger.warning("[service] marked %d interrupted RUNNING jobs as INTERRUPTED", interrupted)

        logger.info(
            "[service] start repositories=%d interval=%ss workers=%d",
            len(self.scanner.repositories()),
            interval,
            self.config.max_concurrent_jobs,
        )

        scanner_thread = threading.Thread(target=self._scan_loop, args=(interval,), daemon=True)
        scanner_thread.start()

        workers = max(1, self.config.max_concurrent_jobs)
        futures: set = set()
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="worker") as pool:
            while not self._stop.is_set():
                while len(futures) >= workers * 2:
                    done = {f for f in futures if f.done()}
                    futures -= done
                    for f in done:
                        f.result()
                    time.sleep(0.5)
                job = self.db.next_runnable_job()
                if job is None:
                    time.sleep(1)
                    continue
                futures.add(pool.submit(self.worker.try_process, job))
            for f in futures:
                f.result()

    def _scan_loop(self, interval: int) -> None:
        next_scan = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_scan:
                try:
                    self.scan_now()
                except Exception as exc:  # noqa: BLE001
                    logger.error("[service] scan failed: %s", exc)
                next_scan = now + interval
            wait = min(max(next_scan - time.monotonic(), 0), 60)
            if self._stop.wait(wait):
                return

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        def _signal(_signum, _frame) -> None:  # type: ignore[no-untyped-def]
            logger.info("[service] signal received; shutting down")
            self.stop()

        try:
            signal.signal(signal.SIGINT, _signal)
            signal.signal(signal.SIGTERM, _signal)
        except (ValueError, OSError):  # pragma: no cover - not on main thread
            pass
        self.run()
        logger.info("[service] stopped")
