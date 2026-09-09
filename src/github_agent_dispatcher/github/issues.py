from __future__ import annotations

import logging
from collections.abc import Iterator

from github_agent_dispatcher.github.client import (
    Comment,
    GitHubAPIError,
    GitHubClient,
    Issue,
    PullRequest,
    ReviewComment,
)

logger = logging.getLogger(__name__)


def issue_trigger_candidates(client: GitHubClient, repo: str) -> Iterator[tuple[Issue, list[Comment]]]:
    """Yield each open issue in ``repo`` with its comments."""
    try:
        issues = client.list_issues(repo, state="open")
    except GitHubAPIError as exc:
        logger.warning("[issues] could not list issues for %s: %s", repo, exc)
        return
    for issue in issues:
        comments = _safe_list_comments(client, repo, issue.number)
        yield issue, comments


def pr_trigger_candidates(
    client: GitHubClient, repo: str
) -> Iterator[tuple[PullRequest, list[Comment], list[ReviewComment]]]:
    try:
        prs = client.list_pull_requests(repo, state="open")
    except GitHubAPIError as exc:
        logger.warning("[prs] could not list pull requests for %s: %s", repo, exc)
        return
    for pr in prs:
        comments = _safe_list_comments(client, repo, pr.number)
        reviews = _safe_list_reviews(client, repo, pr.number)
        yield pr, comments, reviews


def _safe_list_comments(client: GitHubClient, repo: str, number: int) -> list[Comment]:
    try:
        return client.list_issue_comments(repo, number)
    except GitHubAPIError as exc:
        logger.warning("[prs] could not list comments for %s#%s: %s", repo, number, exc)
        return []


def _safe_list_reviews(client: GitHubClient, repo: str, number: int) -> list[ReviewComment]:
    try:
        return client.list_review_comments(repo, number)
    except GitHubAPIError as exc:
        logger.warning("[prs] could not list review comments for %s#%s: %s", repo, number, exc)
        return []
