from __future__ import annotations

import pytest


def test_queue_smoke(queue, make_job):
    job = make_job()
    queue.enqueue(job)
    assert queue.is_processed("issue", "owner/repo1", "issue:#123") is True
    assert queue.next_job().id == job.id


def test_retry_requeues_failed(queue, make_job):
    job = make_job(status="FAILED", error="boom")
    queue.enqueue(job)
    got = queue.retry(job.id)
    assert got is not None
    assert got.status == "QUEUED"
    assert got.error is None


def test_retry_rejects_succeeded(queue, make_job):
    job = make_job(status="SUCCEEDED")
    queue.enqueue(job)
    with pytest.raises(ValueError):
        queue.retry(job.id)


def test_cancel_queued(queue, make_job):
    job = make_job(status="QUEUED")
    queue.enqueue(job)
    got = queue.cancel(job.id)
    assert got.status == "CANCELLED"


def test_cancel_rejects_succeeded(queue, make_job):
    job = make_job(status="SUCCEEDED")
    queue.enqueue(job)
    with pytest.raises(ValueError):
        queue.cancel(job.id)


def test_unblock_requeues(queue, make_job):
    job = make_job(status="BLOCKED")
    queue.enqueue(job)
    got = queue.unblock(job.id)
    assert got.status == "QUEUED"


def test_unblock_rejects_queued(queue, make_job):
    job = make_job(status="QUEUED")
    queue.enqueue(job)
    with pytest.raises(ValueError):
        queue.unblock(job.id)
