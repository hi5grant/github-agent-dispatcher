from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from github_agent_dispatcher.jobs.models import Job, JobStatus, JobType
except Exception:  # pragma: no cover - circular import guard during early import
    Job = Any  # type: ignore
    JobStatus = Any  # type: ignore
    JobType = Any  # type: ignore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS processed_items (
                topic TEXT NOT NULL,
                repo TEXT NOT NULL,
                item_id TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (topic, repo, item_id)
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                repo TEXT NOT NULL,
                issue_number INTEGER,
                pull_number INTEGER,
                branch TEXT,
                comment_id INTEGER,
                requester TEXT,
                request_text TEXT,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                commit_sha TEXT,
                pr_url TEXT,
                context_json TEXT,
                tries INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_repo ON jobs(repo);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ meta
    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else row["value"]

    # ------------------------------------------------------------ processed
    def mark_processed(self, topic: str, repo: str, item_id: str) -> None:
        now = utc_now()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO processed_items(topic, repo, item_id, processed_at) "
                "VALUES (?, ?, ?, ?)",
                (topic, repo, str(item_id), now),
            )
            self._conn.commit()

    def is_processed(self, topic: str, repo: str, item_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM processed_items WHERE topic = ? AND repo = ? AND item_id = ?",
                (topic, repo, str(item_id)),
            ).fetchone()
        return row is not None

    def unmark_processed(self, topic: str, repo: str, item_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM processed_items WHERE topic = ? AND repo = ? AND item_id = ?",
                (topic, repo, str(item_id)),
            )
            self._conn.commit()

    # ---------------------------------------------------------------- jobs
    def _job_from_row(self, row: sqlite3.Row) -> Job:
        from github_agent_dispatcher.jobs.models import Job

        return Job(
            id=row["id"],
            type=row["type"],
            repo=row["repo"],
            issue_number=row["issue_number"],
            pull_number=row["pull_number"],
            branch=row["branch"],
            comment_id=row["comment_id"],
            requester=row["requester"],
            request_text=row["request_text"],
            status=row["status"],
            result=row["result"],
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            commit_sha=row["commit_sha"],
            pr_url=row["pr_url"],
            context=json.loads(row["context_json"] or "{}"),
            tries=row["tries"],
        )

    def insert_job(self, job: Job) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs(id, type, repo, issue_number, pull_number, branch, comment_id, "
                "requester, request_text, status, result, error, created_at, started_at, "
                "completed_at, commit_sha, pr_url, context_json, tries) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.id,
                    job.type,
                    job.repo,
                    job.issue_number,
                    job.pull_number,
                    job.branch,
                    job.comment_id,
                    job.requester,
                    job.request_text,
                    job.status,
                    job.result,
                    job.error,
                    job.created_at,
                    job.started_at,
                    job.completed_at,
                    job.commit_sha,
                    job.pr_url,
                    json.dumps(job.context),
                    job.tries,
                ),
            )
            self._conn.commit()

    def update_job(self, job: Job) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET type = ?, repo = ?, issue_number = ?, pull_number = ?, "
                "branch = ?, comment_id = ?, requester = ?, request_text = ?, status = ?, "
                "result = ?, error = ?, started_at = ?, completed_at = ?, commit_sha = ?, "
                "pr_url = ?, context_json = ?, tries = ? WHERE id = ?",
                (
                    job.type,
                    job.repo,
                    job.issue_number,
                    job.pull_number,
                    job.branch,
                    job.comment_id,
                    job.requester,
                    job.request_text,
                    job.status,
                    job.result,
                    job.error,
                    job.started_at,
                    job.completed_at,
                    job.commit_sha,
                    job.pr_url,
                    json.dumps(job.context),
                    job.tries,
                    job.id,
                ),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def list_jobs(
        self, status: str | None = None, limit: int | None = None, repo: str | None = None
    ) -> list[Job]:
        query = "SELECT * FROM jobs"
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if repo:
            clauses.append("repo = ?")
            params.append(repo)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._job_from_row(r) for r in rows]

    def next_runnable_job(self) -> Job | None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status IN ('DISCOVERED', 'QUEUED') "
                "ORDER BY created_at ASC, id ASC LIMIT 1"
            ).fetchall()
        if not rows:
            return None
        return self._job_from_row(rows[0])

    def claim_job(self, job_id: str) -> Job | None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = 'RUNNING', started_at = ? WHERE id = ? AND "
                "status IN ('DISCOVERED', 'QUEUED', 'RUNNING')",
                (utc_now(), job_id),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def fail_interrupted(self, job_id: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status = 'INTERRUPTED', error = ?, completed_at = ? "
                "WHERE id = ? AND status = 'RUNNING'",
                (message, utc_now(), job_id),
            )
            self._conn.commit()

    def reset_interrupted(self) -> int:
        count = 0
        with self._lock:
            for row in self._conn.execute("SELECT id FROM jobs WHERE status = 'RUNNING'").fetchall():
                self._conn.execute(
                    "UPDATE jobs SET status = 'INTERRUPTED', error = ?, completed_at = ? "
                    "WHERE id = ? AND status = 'RUNNING'",
                    ("interrupted by dispatcher restart", utc_now(), row["id"]),
                )
                count += 1
            self._conn.commit()
        return count

    def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._lock:
            for row in self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall():
                counts[row["status"]] = row["n"]
        return counts

    def count_repos(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(DISTINCT repo) AS n FROM jobs").fetchone()
        return int(row["n"]) if row else 0
