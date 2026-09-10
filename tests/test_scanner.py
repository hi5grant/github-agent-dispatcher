from __future__ import annotations

from github_agent_dispatcher.github.client import (
    Comment,
    Issue,
    PullRequest,
    RepoInfo,
    ReviewComment,
)
from github_agent_dispatcher.jobs.models import JobType
from github_agent_dispatcher.polling.scanner import Scanner


def repo(name="repo1", owner="owner"):
    return RepoInfo(
        owner=owner,
        name=name,
        repo_url=f"https://github.com/{owner}/{name}",
        clone_url=f"https://github.com/{owner}/{name}.git",
        default_branch="main",
        private=False,
    )


def issue(number=1, owner="owner", name="repo1", user="owner", body="", title="t", labels=None, state="open"):
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
    )


def comment(cid, number=1, user="owner", body="@agent do it", owner="owner", name="repo1", repo=None):
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


def pr(number=1, state="open", head_ref="feature-x", user="owner"):
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
        head_clone_url="https://github.com/owner/repo1.git",
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


def make_scanner(config, fg):
    from github_agent_dispatcher.jobs.queue import JobQueue
    from github_agent_dispatcher.storage.database import Database

    db = Database(config.database_path)
    queue = JobQueue(db)
    return Scanner(config, fg, queue), db, queue


# ---------------------------------------------------------------- issues
def test_issue_body_mention_discovered(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue(body="@agent implement the fix")]
    scanner, db, queue = make_scanner(config, fake_github)
    result = scanner.scan()
    assert result["discovered"] == 1
    jobs = db.list_jobs()
    assert jobs[0].type == JobType.ISSUE
    assert jobs[0].issue_number == 1
    db.close()


def test_issue_without_trigger_not_discovered(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue(body="just an ordinary bug report")]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    db.close()


def test_issue_by_unauthorized_user_not_discovered(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue(user="mallory", body="@agent please")]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    db.close()


def test_issue_by_allowed_user_discovered(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [
        Issue(  # noqa: E501
            owner="owner",
            name="repo1",
            number=2,
            title="t",
            body="@agent please",
            url="https://github.com/owner/repo1/issues/2",
            user="alice",
            state="open",
            labels=[],
        )
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    db.close()


def test_duplicate_scan_only_discovers_once(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue(body="@agent implement fix")]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    assert scanner.scan()["discovered"] == 0
    assert len(db.list_jobs()) == 1
    db.close()


def test_issue_assigned_mode(config, fake_github):
    config.issue_discovery = "assigned"
    fake_github.repos = [repo()]
    fake_github.issues = [
        Issue(  # noqa: E501
            owner="owner",
            name="repo1",
            number=3,
            title="t",
            body="",
            url="https://github.com/owner/repo1/issues/3",
            user="someone",
            state="open",
            labels=[],
            assignees=["owner"],
        )
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    db.close()


def test_issue_label_agent_mode(config, fake_github):
    config.issue_discovery = "label:agent"
    fake_github.repos = [repo()]
    fake_github.issues = [
        Issue(  # noqa: E501
            owner="owner",
            name="repo1",
            number=4,
            title="t",
            body="",
            url="https://github.com/owner/repo1/issues/4",
            user="owner",
            state="open",
            labels=["agent"],
        )
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    db.close()


# ------------------------------------------------------------- comments
def test_issue_comment_trigger_discovered(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue()]
    fake_github.issue_comments = [comment(100, number=1, body="@agent fix the problem")]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    jobs = db.list_jobs()
    assert jobs[0].type == JobType.ISSUE_COMMENT
    assert jobs[0].comment_id == 100
    db.close()


def test_issue_comment_without_trigger_not_discovered(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue()]
    fake_github.issue_comments = [comment(101, number=1, body="nice work")]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    db.close()


def test_unauthorized_comment_not_discovered(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue()]
    fake_github.issue_comments = [comment(102, number=1, user="mallory", body="@agent do it")]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    db.close()


def test_dispatcher_comments_do_not_loop(config, fake_github):
    # The dispatcher's own "🤖 Agent completed" comment must never trigger (no @agent).
    fake_github.repos = [repo()]
    fake_github.issues = [issue()]
    fake_github.issue_comments = [
        comment(200, number=1, user="owner", body="🤖 Agent completed this request.")
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    db.close()


# --------------------------------------------------------- fresh-db reconcile
def test_fresh_db_reconciles_resolved_issue_comment(config, fake_github):
    # A brand-new SQLite re-scans everything; a resolution marker on the thread
    # must prevent the item from being re-enqueued and record it as processed.
    fake_github.repos = [repo()]
    fake_github.issues = [issue()]
    fake_github.issue_comments = [
        comment(100, number=1, body="@agent fix the problem"),
        comment(
            101,
            number=1,
            body=(
                "Resolves comment 100 (github-agent-dispatcher job aaabbb)\n"
                "<!-- gad-resolved topic=issue_comment repo=owner/repo1 item=comment:100 job=aaabbb -->"
            ),
        ),
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    assert db.is_processed("issue_comment", "owner/repo1", "comment:100")
    db.close()


def test_fresh_db_reconciles_resolved_issue(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue(body="@agent implement the fix")]
    fake_github.issue_comments = [
        comment(
            1,
            number=1,
            body="<!-- gad-resolved topic=issue repo=owner/repo1 item=issue:#1 job=aaaa -->",
        )
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    assert db.is_processed("issue", "owner/repo1", "issue:#1")
    db.close()


def test_fresh_db_reconciles_resolved_pull_comment(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.prs = [pr()]
    fake_github.issue_comments = [
        comment(300, number=1, body="@agent address this review comment"),
        comment(
            301,
            number=1,
            body="<!-- gad-resolved topic=pull_comment repo=owner/repo1 item=comment:300 job=bbbb -->",
        ),
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    assert db.is_processed("pull_comment", "owner/repo1", "comment:300")
    db.close()


def test_fresh_db_reconciles_resolved_review_comment(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.prs = [pr()]
    fake_github.review_comments = [review(400)]
    fake_github.issue_comments = [
        comment(
            301,
            number=1,
            body="Resolves comment 400 (github-agent-dispatcher job cccc)\n"
            "<!-- gad-resolved topic=review_comment repo=owner/repo1 item=comment:400 job=cccc -->",
        )
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    assert db.is_processed("review_comment", "owner/repo1", "comment:400")
    db.close()


def test_fresh_db_marker_for_other_comment_still_discovers(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue()]
    fake_github.issue_comments = [
        comment(100, number=1, body="@agent fix the problem"),
        comment(
            102,
            number=1,
            body="<!-- gad-resolved topic=issue_comment repo=owner/repo1 item=comment:999 job=aaaa -->",
        ),
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    jobs = db.list_jobs()
    assert jobs[0].type == JobType.ISSUE_COMMENT
    assert jobs[0].comment_id == 100
    db.close()


def test_fresh_db_marker_for_other_repo_does_not_suppress(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.issues = [issue()]
    fake_github.issue_comments = [
        comment(100, number=1, body="@agent fix the problem"),
        comment(
            102,
            number=1,
            body="<!-- gad-resolved topic=issue_comment repo=other/repo item=comment:100 job=aaaa -->",
        ),
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    db.close()


# ------------------------------------------------------------------ prs
def test_pr_comment_discovered_with_branch(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.prs = [pr(head_ref="feature-branch")]
    fake_github.issue_comments = [comment(300, number=1, body="@agent address this review comment")]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    job = db.list_jobs()[0]
    assert job.type == JobType.PULL_COMMENT
    assert job.pull_number == 1
    assert job.branch == "feature-branch"
    db.close()


def test_closed_pr_ignored(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.prs = [pr(state="closed")]
    fake_github.issue_comments = [comment(301, number=1, body="@agent address this review comment")]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 0
    db.close()


def test_review_comment_discovered(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.prs = [pr()]
    fake_github.review_comments = [review(400)]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    job = db.list_jobs()[0]
    assert job.type == JobType.REVIEW_COMMENT
    assert job.pull_number == 1
    db.close()


def test_pr_comment_dedup_across_scans(config, fake_github):
    fake_github.repos = [repo()]
    fake_github.prs = [pr()]
    fake_github.issue_comments = [comment(500, number=1, body="@agent address this review comment")]
    scanner, db, queue = make_scanner(config, fake_github)
    assert scanner.scan()["discovered"] == 1
    assert scanner.scan()["discovered"] == 0
    assert len(db.list_jobs()) == 1
    db.close()


def test_owned_discovery_mode(config, fake_github):
    config.repository_discovery = "owned"
    config.allowed_repos = []
    fake_github.repos = [
        repo(name="a", owner="owner"),
        repo(name="b", owner="owner"),
    ]
    scanner, db, queue = make_scanner(config, fake_github)
    repos = scanner.repositories()
    assert {f"{r.owner}/{r.name}" for r in repos} == {"owner/a", "owner/b"}
    db.close()


def test_owned_discovery_respects_allowlist(config, fake_github):
    config.repository_discovery = "owned"
    config.allowed_repos = ["owner/a"]
    fake_github.repos = [repo(name="a", owner="owner"), repo(name="b", owner="owner")]
    scanner, db, queue = make_scanner(config, fake_github)
    repos = scanner.repositories()
    assert [f"{r.owner}/{r.name}" for r in repos] == ["owner/a"]
    db.close()
