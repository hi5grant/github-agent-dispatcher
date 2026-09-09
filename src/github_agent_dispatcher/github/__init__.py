"""GitHub domain modules: repository discovery and authorization-safe access."""

from github_agent_dispatcher.github.client import (
    Comment,
    GitHubAPIError,
    GitHubClient,
    Issue,
    PullRequest,
    RateLimitedError,
    RateLimitStatus,
    RepoInfo,
    ReviewComment,
)
from github_agent_dispatcher.github.repositories import resolve_repositories

__all__ = [
    "Comment",
    "GitHubAPIError",
    "GitHubClient",
    "Issue",
    "PullRequest",
    "RateLimitedError",
    "RateLimitStatus",
    "RepoInfo",
    "ReviewComment",
    "resolve_repositories",
]
