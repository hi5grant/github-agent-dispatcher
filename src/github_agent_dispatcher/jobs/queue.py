from __future__ import annotations

from github_agent_dispatcher.jobs.models import Job, JobStatus
from github_agent_dispatcher.storage.database import Database


class JobQueue:
    """Persistent queue backed by SQLite; survives restarts."""

    def __init__(self, db: Database):
        self._db = db

    def enqueue(self, job: Job) -> None:
        self._db.insert_job(job)
        self._db.mark_processed(job.dedup_topic, job.repo, job.dedup_id)

    def item_is_processed(self, job: Job) -> bool:
        return self._db.is_processed(job.dedup_topic, job.repo, job.dedup_id)

    def get(self, job_id: str) -> Job | None:
        return self._db.get_job(job_id)

    def next_job(self) -> Job | None:
        return self._db.next_runnable_job()

    def is_processed(self, topic: str, repo: str, item_id: str) -> bool:
        return self._db.is_processed(topic, repo, item_id)

    def mark_processed(self, topic: str, repo: str, item_id: str) -> None:
        self._db.mark_processed(topic, repo, item_id)

    def unmark_processed(self, topic: str, repo: str, item_id: str) -> None:
        self._db.unmark_processed(topic, repo, item_id)

    def list(self, status: str | None = None, limit: int | None = None, repo: str | None = None) -> list[Job]:
        return self._db.list_jobs(status=status, limit=limit, repo=repo)

    def claim(self, job_id: str) -> Job | None:
        return self._db.claim_job(job_id)

    def retry(self, job_id: str) -> Job | None:
        job = self._db.get_job(job_id)
        if job is None:
            return None
        if job.status not in {
            JobStatus.FAILED,
            JobStatus.BLOCKED,
            JobStatus.CANCELLED,
            JobStatus.INTERRUPTED,
        }:
            raise ValueError(
                f"job {job_id} is {job.status}; only failed/blocked/cancelled jobs can be retried"
            )
        job.status = JobStatus.QUEUED
        job.error = None
        job.result = None
        job.completed_at = None
        job.started_at = None
        job.tries += 1
        self._db.update_job(job)
        return job

    def cancel(self, job_id: str) -> Job | None:
        job = self._db.get_job(job_id)
        if job is None:
            return None
        if job.status not in {JobStatus.DISCOVERED, JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.BLOCKED}:
            raise ValueError(f"job {job_id} is {job.status}; cannot cancel")
        job.status = JobStatus.CANCELLED
        job.completed_at = Job.now()
        self._db.update_job(job)
        return job

    def unblock(self, job_id: str) -> Job | None:
        job = self._db.get_job(job_id)
        if job is None:
            return None
        if job.status != JobStatus.BLOCKED:
            raise ValueError(f"job {job_id} is {job.status}; only BLOCKED jobs can be unblocked")
        job.status = JobStatus.QUEUED
        job.tries += 1
        self._db.update_job(job)
        return job

    def save(self, job: Job) -> None:
        self._db.update_job(job)

    def count_by_status(self) -> dict[str, int]:
        return self._db.count_by_status()
