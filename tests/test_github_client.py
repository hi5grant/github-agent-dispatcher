from __future__ import annotations

import pytest

from github_agent_dispatcher.github.client import GitHubAPIError, GitHubClient, RateLimitedError


class FakeTransport:
    """Routes requests to canned JSON responses by (method, path)."""

    def __init__(self):
        self.responses = {}
        self.calls = []

    def respond(self, method, path, payload, status=200):
        self.responses[(method.upper(), path)] = (status, payload)

    def respond_raw(self, method, path, response):
        self.responses[(method.upper(), path)] = (response.status_code, response)

    def __call__(self, method, url, params=None, json=None, timeout=None):
        self.calls.append((method.upper(), url, params))
        path = url.split("api.github.com")[-1] if "api.github.com" in url else url
        key = (method.upper(), path)
        status, payload = self.responses.get(key, (404, {"message": "not found"}))
        if isinstance(payload, FakeResponse):
            return payload
        return FakeResponse(status, payload)


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self.headers = {}
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    @property
    def content(self):
        return str(self._payload).encode()


@pytest.fixture
def client(tmp_path):
    c = GitHubClient("token", "https://api.github.com")
    transport = FakeTransport()
    c.session.request = transport
    c._transport = transport
    return c


def test_authenticated_user(client):
    client._transport.respond("GET", "/user", {"login": "sameer"})
    assert client.authenticated_user() == "sameer"


def test_list_paginated(client):
    client._transport.respond(
        "GET",
        "/user/repos",
        [
            {
                "owner": {"login": "o"},
                "name": "r",
                "html_url": "h",
                "clone_url": "c",
                "default_branch": "main",
                "private": False,
            }
        ],
    )
    repos = client.list_owned_repositories()
    assert len(repos) == 1
    assert repos[0].owner == "o"
    assert repos[0].name == "r"


def test_rate_limit_parses_retry_after(client):
    resp = FakeResponse(403, {"message": "API rate limit exceeded"})
    resp.headers["Retry-After"] = "123"
    client._transport.respond_raw("GET", "/rate_limit", resp)
    with pytest.raises(RateLimitedError) as exc_info:
        client.get("/rate_limit")
    assert exc_info.value.retry_after == 123.0


def test_rate_limit_default_when_no_header(client):
    client._transport.respond(
        "GET",
        "/rate_limit",
        {"message": "API rate limit exceeded"},
        status=403,
    )
    with pytest.raises(RateLimitedError) as exc_info:
        client.get("/rate_limit")
    assert exc_info.value.retry_after == 60.0


def test_404_raises(client):
    with pytest.raises(GitHubAPIError) as exc:
        client.get("/repos/nope/nope")
    assert exc.value.status_code == 404


def test_post_comment_builds_url(client):
    client._transport.respond(
        "POST",
        "/repos/o/r/issues/3/comments",
        {"id": 9, "html_url": "z", "user": {"login": "u"}, "body": "body text"},
    )
    comment = client.post_comment("o/r", 3, "body text")
    assert comment.id == 9
    assert comment.user == "u"
    assert comment.body == "body text"


def test_list_issues_filters_prs_and_fields(client):
    client._transport.respond(
        "GET",
        "/repos/o/r/issues",
        [
            {
                "number": 1,
                "title": "t1",
                "body": "b",
                "html_url": "h",
                "user": {"login": "u"},
                "state": "open",
                "labels": [{"name": "bug"}],
                "assignees": [{"login": "u"}],
            },
            {
                "number": 2,
                "pull_request": {"url": "u"},
                "title": "PR",
                "body": "",
                "html_url": "h",
                "user": {"login": "u"},
                "state": "open",
                "labels": [],
                "assignees": [],
            },
        ],
    )
    issues = client.list_issues("o/r")
    assert len(issues) == 1
    assert issues[0].number == 1
    assert issues[0].labels == ["bug"]
    assert issues[0].assignees == ["u"]


def test_get_pull_request_parses_head(client):
    client._transport.respond(
        "GET",
        "/repos/o/r/pulls/4",
        {
            "number": 4,
            "title": "t",
            "body": "",
            "html_url": "h",
            "user": {"login": "u"},
            "state": "open",
            "head": {"ref": "fix", "sha": "s", "repo": {"full_name": "o/r", "clone_url": "cu"}},
            "base": {"ref": "main", "sha": "s2"},
        },
    )
    pr = client.get_pull_request("o/r", 4)
    assert pr.head_ref == "fix"
    assert pr.head_repo_full_name == "o/r"
    assert pr.head_clone_url == "cu"
    assert pr.base_ref == "main"


def test_list_review_comments(client):
    client._transport.respond(
        "GET",
        "/repos/o/r/pulls/4/comments",
        [
            {
                "id": 1,
                "html_url": "h",
                "user": {"login": "u"},
                "body": "@agent fix",
                "created_at": "t",
                "path": "a.py",
                "position": 2,
            }
        ],
    )
    reviews = client.list_review_comments("o/r", 4)
    assert reviews[0].id == 1
    assert reviews[0].path == "a.py"


def test_transient_502_retries(client):

    client._transport.calls.clear()
    counted = {"n": 0}

    def flaky(method, url, params=None, json=None, timeout=None):
        counted["n"] += 1
        if counted["n"] < 3:
            return FakeResponse(502, {"message": "bad gateway"})
        return FakeResponse(200, {"login": "ok"})

    client.session.request = flaky
    assert client.authenticated_user() == "ok"
    assert counted["n"] == 3
