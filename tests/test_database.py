from __future__ import annotations

from github_agent_dispatcher.jobs.models import JobStatus
from github_agent_dispatcher.storage.database import Database


def test_processed_marking(db: Database, make_job):
    job = make_job(comment_id=42)
    assert db.is_processed(job.dedup_topic, job.repo, job.dedup_id) is False
    db.mark_processed(job.dedup_topic, job.repo, job.dedup_id)
    assert db.is_processed(job.dedup_topic, job.repo, job.dedup_id) is True


def test_processed_unmarking(db: Database, make_job):
    job = make_job(comment_id=42)
    db.mark_processed(job.dedup_topic, job.repo, job.dedup_id)
    db.unmark_processed(job.dedup_topic, job.repo, job.dedup_id)
    assert db.is_processed(job.dedup_topic, job.repo, job.dedup_id) is False


def test_job_roundtrip(db: Database, make_job):
    job = make_job(context={"issue_url": "https://x"})
    db.insert_job(job)
    loaded = db.get_job(job.id)
    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.repo == "owner/repo1"
    assert loaded.issue_number == 123
    assert loaded.context["issue_url"] == "https://x"


def test_job_update(db: Database, make_job):
    job = make_job()
    db.insert_job(job)
    job.status = JobStatus.RUNNING
    job.commit_sha = "abc123"
    db.update_job(job)
    loaded = db.get_job(job.id)
    assert loaded.status == JobStatus.RUNNING
    assert loaded.commit_sha == "abc123"


def test_jobs_survive_restart(db: Database, make_job):
    job = make_job()
    db.insert_job(job)
    db2 = Database(db.path)
    loaded = db2.get_job(job.id)
    assert loaded is not None
    assert loaded.status == JobStatus.QUEUED
    db2.close()


def test_reset_interrupted(db: Database, make_job):
    running = make_job(id="r1", status=JobStatus.RUNNING)
    done = make_job(id="d1", status=JobStatus.SUCCEEDED)
    queued = make_job(id="q1", status=JobStatus.QUEUED)
    for j in (running, done, queued):
        db.insert_job(j)
    count = db.reset_interrupted()
    assert count == 1
    assert db.get_job("r1").status == JobStatus.INTERRUPTED
    assert db.get_job("d1").status == JobStatus.SUCCEEDED
    assert db.get_job("q1").status == JobStatus.QUEUED


def test_next_runnable_job_ordering(db: Database, make_job):
    a = make_job(id="a1", status=JobStatus.QUEUED)
    b = make_job(id="b1", status=JobStatus.DISCOVERED)
    c = make_job(id="c1", status=JobStatus.BLOCKED)
    d = make_job(id="d1", status=JobStatus.RUNNING)
    for j in (a, b, c, d):
        db.insert_job(j)
    nxt = db.next_runnable_job()
    assert nxt.id == "a1"


def test_claim_job(db: Database, make_job):
    job = make_job(id="c1", status=JobStatus.QUEUED)
    db.insert_job(job)
    claimed = db.claim_job("c1")
    assert claimed.status == JobStatus.RUNNING
    assert claimed.started_at is not None
    assert db.get_job("c1").status == JobStatus.RUNNING


def test_count_by_status(db: Database, make_job):
    db.insert_job(make_job(id="x1", status=JobStatus.QUEUED))
    db.insert_job(make_job(id="x2", status=JobStatus.SUCCEEDED))
    db.insert_job(make_job(id="x3", status=JobStatus.SUCCEEDED))
    counts = db.count_by_status()
    assert counts == {"QUEUED": 1, "SUCCEEDED": 2}


def test_meta_roundtrip(db: Database):
    db.set_meta("last_scan_at", "2026-01-01T00:00:00+00:00")
    assert db.get_meta("last_scan_at") == "2026-01-01T00:00:00+00:00"
    assert db.get_meta("missing", "x") == "x"
