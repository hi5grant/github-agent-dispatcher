from __future__ import annotations

from github_agent_dispatcher.jobs.models import Job, JobStatus, new_job_id
from github_agent_dispatcher.reconciliation import (
    marker_lines,
    parse_resolution_markers,
    resolution_marker,
    resolved_phrase,
)


def job(**kwargs):
    defaults = dict(
        id="aaabbb",
        type="issue_comment",
        repo="owner/repo1",
        issue_number=1,
        pull_number=None,
        branch=None,
        comment_id=123456,
        requester="owner",
        request_text="@agent do it",
        status=JobStatus.SUCCEEDED,
        created_at=Job.now(),
    )
    defaults.update(kwargs)
    return Job(**defaults)


def test_marker_lines_for_comment_job():
    human, tag = marker_lines(job())
    assert human == "Resolves comment 123456 (github-agent-dispatcher job aaabbb)"
    assert "gad-resolved" in tag
    assert parse_resolution_markers(f"{human}\n{tag}") == {("issue_comment", "owner/repo1", "comment:123456")}


def test_marker_lines_for_issue_job():
    human, tag = marker_lines(job(type="issue", comment_id=None, issue_number=5))
    assert human == "Resolves issue #5 (github-agent-dispatcher job aaabbb)"
    assert parse_resolution_markers(tag) == {("issue", "owner/repo1", "issue:#5")}


def test_parse_ignores_unrelated_text():
    assert parse_resolution_markers("no marker here @agent") == set()
    assert parse_resolution_markers("") == set()


def test_parse_extracts_multiple_markers():
    body = f"{resolution_marker(job(comment_id=1))}\n{resolution_marker(job(comment_id=2))}"
    assert parse_resolution_markers(body) == {
        ("issue_comment", "owner/repo1", "comment:1"),
        ("issue_comment", "owner/repo1", "comment:2"),
    }


def test_round_trip_with_real_job_id():
    j = job(id=new_job_id())
    parsed = parse_resolution_markers(f"{resolved_phrase(j)}\n{resolution_marker(j)}")
    assert parsed == {("issue_comment", "owner/repo1", "comment:123456")}
