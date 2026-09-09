from __future__ import annotations

from github_agent_dispatcher.github.client import (
    Comment,
    Issue,
    PullRequest,
    RepoInfo,
    ReviewComment,
)


def repo_info(name="repo1", owner="owner", clone_url=None, default_branch="main"):
    return RepoInfo(
        owner=owner,
        name=name,
        repo_url=f"https://github.com/{owner}/{name}",
        clone_url=clone_url or f"https://github.com/{owner}/{name}.git",
        default_branch=default_branch,
        private=False,
    )


def issue(
    number=1,
    owner="owner",
    name="repo1",
    user="owner",
    body="",
    title="t",
    labels=None,
    assignees=None,
    state="open",
):
    return Issue(
        owner=owner,
        name=name,
        number=number,
        title=title,
        body=body,
        url=f"https://github.com/{owner}/{name}/issues/{number}",
        user=user,
        state=state,
        labels=labels or [],
        assignees=assignees or [],
    )


def comment(
    cid,
    number=1,
    user="owner",
    body="@agent do it",
    owner="owner",
    name="repo1",
):
    return Comment(
        owner=owner,
        name=name,
        number=number,
        id=cid,
        url="https://github.com/x",
        user=user,
        body=body,
        created_at="2026-01-01T00:00:00Z",
    )


def pull_request(number=1, state="open", head_ref="feature-x", user="owner", head_clone_url=None):
    return PullRequest(
        owner="owner",
        name="repo1",
        number=number,
        title="pr",
        body="",
        url=f"https://github.com/owner/repo1/pull/{number}",
        user=user,
        state=state,
        base_ref="main",
        base_sha="b" * 40,
        head_ref=head_ref,
        head_sha="h" * 40,
        head_repo_full_name="owner/repo1",
        head_clone_url=head_clone_url or "https://github.com/owner/repo1.git",
    )


def review(cid, number=1, user="owner", body="@agent fix this"):
    return ReviewComment(
        owner="owner",
        name="repo1",
        number=number,
        id=cid,
        url=f"https://github.com/owner/repo1/pull/{number}#discussion_r{cid}",
        user=user,
        body=body,
        created_at="2026-01-01T00:00:00Z",
        path="src/x.py",
        position=5,
    )


class FakeGitHub:
    """Stands in for GitHubClient with canned data; records outbound calls."""

    def __init__(self, **kwargs):
        self.owner = kwargs.get("owner", "owner")
        self.repos = list(kwargs.get("repos", []))
        self.issues = list(kwargs.get("issues", []))
        self.issue_comments = list(kwargs.get("issue_comments", []))
        self.prs = list(kwargs.get("prs", []))
        self.review_comments = list(kwargs.get("review_comments", []))
        self.posted: list[tuple] = []
        self.pull_requests_created: list[tuple] = []
        self.authenticated_user = kwargs.get("authenticated_user", "owner")

    def authenticated_user(self):
        return self.authenticated_user

    def list_owned_repositories(self):
        return self.repos

    def get_repository(self, name):
        for r in self.repos:
            if f"{r.owner}/{r.name}" == name:
                return r
        raise AssertionError(f"repo not found: {name}")

    def list_issues(self, name, state="open"):
        return [i for i in self.issues if f"{i.owner}/{i.name}" == name and i.state == state]

    def list_issue_comments(self, name, number):
        return [c for c in self.issue_comments if f"{c.owner}/{c.name}" == name and c.number == number]

    def list_pull_requests(self, name, state="open"):
        return [p for p in self.prs if f"{p.owner}/{p.name}" == name and p.state == state]

    def list_review_comments(self, name, number):
        return [c for c in self.review_comments if f"{c.owner}/{c.name}" == name and c.number == number]

    def get_pull_request(self, name, number):
        for p in self.prs:
            if f"{p.owner}/{p.name}" == name and p.number == number:
                return p
        raise AssertionError(f"PR not found: {name}#{number}")

    def post_comment(self, name, number, body):
        self.posted.append((name, number, body))
        return {"id": len(self.posted), "html_url": "https://example.com/c"}

    def create_pull_request(self, name, head, base, title, body):
        self.pull_requests_created.append((name, head, base, title))
        return {"html_url": f"https://github.com/{name}/pull/999"}


class WritingAgent:
    """Fake agent backend that writes a file, yielding a change."""

    def __init__(self, file_name="agent-work.txt", content="greetings from the agent\n"):
        self.file_name = file_name
        self.content = content
        self.calls = 0

    def run(self, request_text, context, cwd):
        self.calls += 1
        (cwd / self.file_name).write_text(self.content)
        from github_agent_dispatcher.agents.base import AgentResult

        return AgentResult(success=True, output="did the thing")


class NoopAgent:
    def run(self, request_text, context, cwd):
        from github_agent_dispatcher.agents.base import AgentResult

        return AgentResult(success=True, output="nothing")


class FailingAgent:
    def run(self, request_text, context, cwd):
        from github_agent_dispatcher.agents.base import AgentResult

        return AgentResult(success=False, error="the model exploded")
