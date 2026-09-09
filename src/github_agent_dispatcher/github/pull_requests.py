from __future__ import annotations

from collections.abc import Iterator

from github_agent_dispatcher.github.client import (
    Comment,
    GitHubClient,
    PullRequest,
    ReviewComment,
)
from github_agent_dispatcher.github.issues import pr_trigger_candidates

__all__ = ["pr_trigger_candidates"]


def pull_request_candidates(
    client: GitHubClient, repo: str
) -> Iterator[tuple[PullRequest, list[Comment], list[ReviewComment]]]:
    """Alias kept for discoverability; see github.issues.pr_trigger_candidates."""
    yield from pr_trigger_candidates(client, repo)
