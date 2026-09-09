from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)


class GitHubAPIError(Exception):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response: requests.Response | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class RateLimitedError(GitHubAPIError):
    def __init__(self, message: str, retry_after: float = 60.0, status_code: int = 403):
        super().__init__(message, status_code)
        self.retry_after = retry_after


def _parse_repo(repo: str) -> tuple[str, str]:
    parts = repo.strip().strip("/").split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"invalid repository name {repo!r}; expected 'owner/repo'")
    return parts[0], parts[1]


@dataclass
class RepoInfo:
    owner: str
    name: str
    repo_url: str
    clone_url: str
    default_branch: str
    private: bool


@dataclass
class Issue:
    owner: str
    name: str
    number: int
    title: str
    body: str
    url: str
    user: str
    state: str
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)


@dataclass
class Comment:
    owner: str
    name: str
    number: int
    id: int
    url: str
    user: str
    body: str
    created_at: str


@dataclass
class PullRequest:
    owner: str
    name: str
    number: int
    title: str
    body: str
    url: str
    user: str
    state: str
    base_ref: str
    base_sha: str
    head_ref: str
    head_sha: str
    head_repo_full_name: str | None
    head_clone_url: str | None
    labels: list[str] = field(default_factory=list)


@dataclass
class ReviewComment:
    owner: str
    name: str
    number: int
    id: int
    url: str
    user: str
    body: str
    created_at: str
    path: str | None = None
    position: int | None = None


@dataclass
class RateLimitStatus:
    core_remaining: int
    core_limit: int
    core_reset: int
    search_remaining: int
    search_limit: int

    @property
    def summary(self) -> str:
        return (
            f"core {self.core_remaining}/{self.core_limit}, "
            f"search {self.search_remaining}/{self.search_limit}"
        )


class GitHubClient:
    def __init__(self, token: str, api_url: str = "https://api.github.com"):
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    # ------------------------------------------------------------------ http
    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json: dict | None = None,
        _retries: int = 4,
    ) -> Any:
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        for attempt in range(_retries):
            try:
                resp = self.session.request(method, url, params=params, json=json, timeout=60)
            except requests.RequestException as exc:
                if attempt < _retries - 1:
                    wait = 5 * (2**attempt)
                    logger.warning("[api] request failed (%s); retrying in %ss", exc, wait)
                    time.sleep(wait)
                    continue
                raise GitHubAPIError(f"network error: {exc}") from exc

            if resp.status_code in (401, 403) and "rate limit" in resp.text.lower():
                retry_after: float = 60.0
                value = resp.headers.get("Retry-After")
                if value and value.isdigit():
                    retry_after = float(value)
                raise RateLimitedError(
                    "GitHub rate limit reached",
                    retry_after=retry_after,
                    status_code=resp.status_code,
                )

            if resp.status_code == 202:
                time.sleep(3)
                continue

            if resp.status_code in (502, 503, 504):
                if attempt < _retries - 1:
                    wait = 5 * (2**attempt)
                    logger.warning("[api] transient %s; retrying in %ss", resp.status_code, wait)
                    time.sleep(wait)
                    continue
                raise GitHubAPIError(f"GitHub returned {resp.status_code}", resp.status_code, resp)

            if resp.status_code >= 400:
                body = resp.text[:500]
                raise GitHubAPIError(
                    f"GitHub API {method} {path} -> {resp.status_code}: {body}",
                    resp.status_code,
                    resp,
                )

            if not resp.content:
                return None
            return resp.json()

    def get(self, path: str, params: dict | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: dict | None = None) -> Any:
        return self.request("POST", path, json=json)

    def patch(self, path: str, json: dict | None = None) -> Any:
        return self.request("PATCH", path, json=json)

    def get_paginated(self, path: str, params: dict | None = None) -> Iterable[Any]:
        page = 1
        per_page = 100
        while True:
            pagination = dict(params or {})
            pagination["per_page"] = per_page
            pagination["page"] = page
            items = self.get(path, params=pagination)
            if not items:
                return
            yield from items
            if len(items) < per_page:
                return
            page += 1

    # ---------------------------------------------------------------- auth
    def authenticated_user(self) -> str:
        data = self.get("/user")
        return data["login"]

    def rate_limit(self) -> RateLimitStatus:
        data = self.get("/rate_limit")
        resources = data["resources"]
        core = resources.get("core", {})
        search = resources.get("search", {})
        return RateLimitStatus(
            core_remaining=int(core.get("remaining", -1)),
            core_limit=int(core.get("limit", -1)),
            core_reset=int(core.get("reset", 0)),
            search_remaining=int(search.get("remaining", -1)),
            search_limit=int(search.get("limit", -1)),
        )

    # ------------------------------------------------------------ repositories
    def list_owned_repositories(self) -> list[RepoInfo]:
        repos: list[RepoInfo] = []
        for item in self.get_paginated("/user/repos", {"affiliation": "owner", "sort": "updated"}):
            repos.append(
                RepoInfo(
                    owner=item["owner"]["login"],
                    name=item["name"],
                    repo_url=item["html_url"],
                    clone_url=item["clone_url"],
                    default_branch=item.get("default_branch", "main"),
                    private=item.get("private", False),
                )
            )
        return repos

    def get_repository(self, repo: str) -> RepoInfo:
        owner, name = _parse_repo(repo)
        item = self.get(f"/repos/{owner}/{name}")
        return RepoInfo(
            owner=item["owner"]["login"],
            name=item["name"],
            repo_url=item["html_url"],
            clone_url=item["clone_url"],
            default_branch=item.get("default_branch", "main"),
            private=item.get("private", False),
        )

    def _parse_label_names(self, labels: list[Any]) -> list[str]:
        return [item["name"] for item in labels] if isinstance(labels, list) else []

    # ---------------------------------------------------------------- issues
    def list_issues(self, repo: str, state: str = "open") -> list[Issue]:
        owner, name = _parse_repo(repo)
        issues: list[Issue] = []
        # ?filter=all does nothing; PRs are filtered out below by the pull_request key.
        for item in self.get_paginated(
            f"/repos/{owner}/{name}/issues",
            {"state": state, "sort": "updated", "direction": "asc"},
        ):
            if item.get("pull_request"):
                continue
            issues.append(
                Issue(
                    owner=owner,
                    name=name,
                    number=item["number"],
                    title=item["title"],
                    body=item.get("body") or "",
                    url=item["html_url"],
                    user=item["user"]["login"] if item.get("user") else "",
                    state=item.get("state", "open"),
                    labels=self._parse_label_names(item.get("labels", [])),
                    assignees=[a["login"] for a in item.get("assignees", []) if a],
                )
            )
        return issues

    def get_issue(self, repo: str, number: int) -> Issue:
        owner, name = _parse_repo(repo)
        item = self.get(f"/repos/{owner}/{name}/issues/{number}")
        return Issue(
            owner=owner,
            name=name,
            number=item["number"],
            title=item["title"],
            body=item.get("body") or "",
            url=item["html_url"],
            user=item["user"]["login"] if item.get("user") else "",
            state=item.get("state", "open"),
            labels=self._parse_label_names(item.get("labels", [])),
            assignees=[a["login"] for a in item.get("assignees", []) if a],
        )

    def list_issue_comments(self, repo: str, number: int) -> list[Comment]:
        owner, name = _parse_repo(repo)
        comments: list[Comment] = []
        for item in self.get_paginated(
            f"/repos/{owner}/{name}/issues/{number}/comments", {"sort": "updated", "direction": "asc"}
        ):
            comments.append(self._to_comment(item, owner, name, number))
        return comments

    def _to_comment(self, item: dict, owner: str, name: str, number: int) -> Comment:
        return Comment(
            owner=owner,
            name=name,
            number=number,
            id=item["id"],
            url=item["html_url"],
            user=item["user"]["login"] if item.get("user") else "",
            body=item.get("body") or "",
            created_at=item.get("created_at", ""),
        )

    def post_comment(self, repo: str, number: int, body: str) -> Comment:
        owner, name = _parse_repo(repo)
        item = self.post(f"/repos/{owner}/{name}/issues/{number}/comments", {"body": body})
        return self._to_comment(item, owner, name, number)

    # -------------------------------------------------------- pull requests
    def list_pull_requests(self, repo: str, state: str = "open") -> list[PullRequest]:
        owner, name = _parse_repo(repo)
        prs: list[PullRequest] = []
        for item in self.get_paginated(
            f"/repos/{owner}/{name}/pulls",
            {"state": state, "sort": "updated", "direction": "asc"},
        ):
            prs.append(self._to_pr(item, owner, name))
        return prs

    def get_pull_request(self, repo: str, number: int) -> PullRequest:
        owner, name = _parse_repo(repo)
        item = self.get(f"/repos/{owner}/{name}/pulls/{number}")
        return self._to_pr(item, owner, name)

    def _to_pr(self, item: dict, owner: str, name: str) -> PullRequest:
        head = item.get("head", {})
        base = item.get("base", {})
        head_repo = head.get("repo") or {}
        return PullRequest(
            owner=owner,
            name=name,
            number=item["number"],
            title=item["title"],
            body=item.get("body") or "",
            url=item["html_url"],
            user=item["user"]["login"] if item.get("user") else "",
            state=item.get("state", "open"),
            base_ref=base.get("ref", ""),
            base_sha=base.get("sha", ""),
            head_ref=head.get("ref", ""),
            head_sha=head.get("sha", ""),
            head_repo_full_name=head_repo.get("full_name"),
            head_clone_url=head_repo.get("clone_url"),
            labels=self._parse_label_names(item.get("labels", [])),
        )

    def list_review_comments(self, repo: str, number: int) -> list[ReviewComment]:
        owner, name = _parse_repo(repo)
        comments: list[ReviewComment] = []
        for item in self.get_paginated(f"/repos/{owner}/{name}/pulls/{number}/comments"):
            comments.append(
                ReviewComment(
                    owner=owner,
                    name=name,
                    number=number,
                    id=item["id"],
                    url=item["html_url"],
                    user=item["user"]["login"] if item.get("user") else "",
                    body=item.get("body") or "",
                    created_at=item.get("created_at", ""),
                    path=item.get("path"),
                    position=item.get("position"),
                )
            )
        return comments

    def create_pull_request(self, repo: str, head: str, base: str, title: str, body: str) -> dict:
        owner, name = _parse_repo(repo)
        return self.post(
            f"/repos/{owner}/{name}/pulls",
            {"title": title, "head": head, "base": base, "body": body},
        )

    def find_pull_request(self, repo: str, branch: str) -> PullRequest | None:
        for pr in self.list_pull_requests(repo, state="all"):
            if pr.head_ref == branch:
                return pr
        return None
