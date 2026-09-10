from __future__ import annotations

import re

from github_agent_dispatcher.jobs.models import Job

#: Machine-readable tag embedded in every resolution comment we post.
RESOLVED_TAG = "gad-resolved"

_RESOLVED_RE = re.compile(
    r"gad-resolved\s+topic=(\S+)\s+repo=(\S+)\s+item=(\S+)\s+job=(\S+)"
)


def resolved_phrase(job: Job) -> str:
    """Human-readable safe word naming the item that initiated the @agent tag."""
    if job.comment_id is not None:
        return f"Resolves comment {job.comment_id} (github-agent-dispatcher job {job.id})"
    return f"Resolves issue #{job.issue_number or '?'} (github-agent-dispatcher job {job.id})"


def resolution_marker(job: Job) -> str:
    """Coded identifier so a fresh database can reconcile from GitHub."""
    return (
        f"<!-- {RESOLVED_TAG} topic={job.dedup_topic} repo={job.repo} "
        f"item={job.dedup_id} job={job.id} -->"
    )


def marker_lines(job: Job) -> tuple[str, str]:
    """Return ``(safe_word_line, machine_tag_line)`` for a feedback comment."""
    return resolved_phrase(job), resolution_marker(job)


def parse_resolution_markers(body: str) -> set[tuple[str, str, str]]:
    """Extract ``(topic, repo, item_id)`` triples referenced by marker comments."""
    found: set[tuple[str, str, str]] = set()
    for match in _RESOLVED_RE.finditer(body or ""):
        found.add((match.group(1), match.group(2), match.group(3)))
    return found
