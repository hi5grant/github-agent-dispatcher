from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from github_agent_dispatcher.config import AppConfig
from tests.fixtures import FakeGitHub


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    state = tmp_path / "state"
    workspace = tmp_path / "workspace"
    state.mkdir(exist_ok=True)
    return AppConfig(
        token="test-token",
        api_url="https://api.github.com",
        github_web_url="https://github.com",
        allowed_repos=["owner/repo1", "owner/repo2"],
        allowed_users=["alice"],
        allow_self=True,
        trigger="@agent",
        agent_command="opencode run",
        agent_extra_args=[],
        workspace_root=workspace,
        poll_interval_seconds=300,
        auto_clone=True,
        auto_create_pr=False,
        database_path=state / "state.db",
        max_concurrent_jobs=2,
        repository_discovery="configured",
        issue_discovery="mention",
        validation_commands=[],
        data_dir=state,
        log_level="INFO",
        token_owner="owner",
        repo_overrides={},
    )


@pytest.fixture
def db(config: AppConfig):
    from github_agent_dispatcher.storage.database import Database

    database = Database(config.database_path)
    yield database
    database.close()


@pytest.fixture
def queue(db):
    from github_agent_dispatcher.jobs.queue import JobQueue

    return JobQueue(db)


@pytest.fixture
def make_job():
    from github_agent_dispatcher.jobs.models import Job, JobStatus, new_job_id

    def _make(**kwargs):
        defaults = dict(
            id=new_job_id(),
            type="issue",
            repo="owner/repo1",
            issue_number=123,
            pull_number=None,
            branch=None,
            comment_id=None,
            requester="owner",
            request_text="@agent fix this",
            status=JobStatus.QUEUED,
            created_at=Job.now(),
            context={},
        )
        defaults.update(kwargs)
        return Job(**defaults)

    return _make


@pytest.fixture
def fake_github():
    return FakeGitHub()


@pytest.fixture
def do_git(tmp_path: Path):
    """Create a bare origin repo containing one seed commit on main."""

    def _setup():
        origin = tmp_path / "origin.git"
        origin.mkdir()
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(origin)],
            check=True,
            capture_output=True,
        )
        scratch = tmp_path / "seed-scratch"
        subprocess.run(
            ["git", "init", "-b", "main", str(scratch)],
            check=True,
            capture_output=True,
        )
        (scratch / "README.md").write_text("# seed\n")
        subprocess.run(["git", "add", "-A"], cwd=str(scratch), check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=Seed", "-c", "user.email=seed@test", "commit", "-m", "seed"],
            cwd=str(scratch),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(origin)], cwd=str(scratch), check=True, capture_output=True
        )
        subprocess.run(["git", "push", "origin", "main"], cwd=str(scratch), check=True, capture_output=True)
        return str(origin)

    return _setup


@pytest.fixture
def git_identity():
    def _set(path: Path):
        subprocess.run(
            ["git", "config", "user.email", "t@t.test"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=path,
            check=True,
            capture_output=True,
        )

    return _set
