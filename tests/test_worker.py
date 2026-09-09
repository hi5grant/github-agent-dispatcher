from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from github_agent_dispatcher.agents.base import AgentResult
from github_agent_dispatcher.jobs.models import Job, JobStatus, new_job_id
from github_agent_dispatcher.jobs.queue import JobQueue
from github_agent_dispatcher.jobs.worker import JobWorker
from github_agent_dispatcher.storage.database import Database
from tests.fixtures import FakeGitHub, NoopAgent, WritingAgent, pull_request, repo_info


def _git_identity_env(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Agent Dispatcher")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "agent@dispatcher.test")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Agent Dispatcher")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "agent@dispatcher.test")


@pytest.fixture
def worker_env(config, do_git, monkeypatch):
    origin = do_git()
    _git_identity_env(monkeypatch)
    info = repo_info(name="repo1", owner="owner", clone_url=origin)
    fg = FakeGitHub(repos=[info])
    db = Database(config.database_path)
    queue = JobQueue(db)
    return {"origin": origin, "github": fg, "db": db, "queue": queue, "config": config}


def seed_branch(origin: str, branch: str, tmp_path: Path) -> None:
    """Push a real branch into the bare origin so the worker can fetch it."""
    scratch = tmp_path / f"seed-{branch.replace('/', '-')}"
    subprocess.run(["git", "init", "-b", "main", str(scratch)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "s@s.test"], cwd=scratch, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Seed"], cwd=scratch, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=scratch, check=True, capture_output=True)
    (scratch / "file.txt").write_text("seed content\n")
    subprocess.run(["git", "add", "-A"], cwd=scratch, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=scratch, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", branch], cwd=scratch, check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", branch], cwd=scratch, check=True, capture_output=True)


class FailingAgentLike:
    def run(self, request_text, context, cwd):
        return AgentResult(success=False, error="the model exploded")


class SlowWritingAgent(WritingAgent):
    def __init__(self, delay=0.3):
        super().__init__()
        self.delay = delay

    def run(self, request_text, context, cwd):
        time.sleep(self.delay)
        return super().run(request_text, context, cwd)


class JobAwareWritingAgent(WritingAgent):
    def run(self, request_text, context, cwd):
        (cwd / self.file_name).write_text(f"{context['_job'].id}\n")
        from github_agent_dispatcher.agents.base import AgentResult

        return AgentResult(success=True, output="did the thing")


class SlowJobAwareWritingAgent(JobAwareWritingAgent):
    def __init__(self, delay=0.3):
        super().__init__()
        self.delay = delay

    def run(self, request_text, context, cwd):
        time.sleep(self.delay)
        return super().run(request_text, context, cwd)


def make_worker(env, backend):
    return JobWorker(env["config"], env["queue"], env["github"], backend, data_dir=env["config"].data_dir)


def make_job(**kwargs):
    defaults = dict(
        id=new_job_id(),
        type="issue",
        repo="owner/repo1",
        issue_number=5,
        pull_number=None,
        branch=None,
        comment_id=None,
        requester="owner",
        request_text="@agent implement a fresh feature",
        status=JobStatus.QUEUED,
        created_at=Job.now(),
        context={"issue_url": "https://github.com/owner/repo1/issues/5"},
    )
    defaults.update(kwargs)
    return Job(**defaults)


def origin_head(origin: str) -> str:
    return subprocess.run(
        ["git", "-C", origin, "log", "--all", "--oneline", "-1"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def has_no_agent_commit(origin: str) -> bool:
    log = subprocess.run(
        ["git", "-C", origin, "log", "--oneline", "--all"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return "agent:" not in log


def test_successful_issue_job_commits_and_pushes(worker_env):
    env = worker_env
    job = make_job()
    env["db"].insert_job(job)
    make_worker(env, WritingAgent()).try_process(job)

    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.SUCCEEDED
    assert done.commit_sha is not None
    assert done.branch == "agent/issue-5"
    assert "agent: address issue #5" in origin_head(env["origin"])
    assert env["github"].posted, "expected a result comment"
    _, page_num, body = env["github"].posted[0]
    assert page_num == 5
    assert "Agent completed" in body


def test_failed_agent_marks_failed(worker_env):
    env = worker_env
    job = make_job()
    env["db"].insert_job(job)
    make_worker(env, FailingAgentLike()).try_process(job)
    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.FAILED
    assert "exploded" in (done.error or "")


def test_agent_with_no_changes_fails(worker_env):
    env = worker_env
    job = make_job()
    env["db"].insert_job(job)
    make_worker(env, NoopAgent()).try_process(job)
    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.FAILED
    assert done.result == "NO_CHANGES"


def test_dirty_workspace_blocks(worker_env, tmp_path):
    env = worker_env
    ws = Path(env["config"].workspace_root) / "owner--repo1"
    ws.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", env["origin"], str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=ws, check=True, capture_output=True)
    (ws / "precious.txt").write_text("do not touch")

    job = make_job()
    env["db"].insert_job(job)
    make_worker(env, WritingAgent()).try_process(job)

    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.BLOCKED
    assert "pre-existing" in (done.error or "")
    assert (ws / "precious.txt").read_text() == "do not touch"
    assert has_no_agent_commit(env["origin"])


def test_auto_clone_disabled_blocks(worker_env):
    env = worker_env
    env["config"].auto_clone = False
    job = make_job()
    env["db"].insert_job(job)
    make_worker(env, WritingAgent()).try_process(job)
    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.BLOCKED
    assert "AUTO_CLONE" in (done.error or "")


def test_validation_failure_does_not_push(worker_env):
    env = worker_env
    env["config"].validation_commands = [f'{sys.executable} -c "raise SystemExit(1)"']
    job = make_job()
    env["db"].insert_job(job)
    make_worker(env, WritingAgent()).try_process(job)
    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.FAILED
    assert "validation" in (done.error or "").lower()
    assert has_no_agent_commit(env["origin"])


def test_validation_passes_then_pushes(worker_env):
    env = worker_env
    env["config"].validation_commands = [f"{sys.executable} -c \"print('ok')\""]
    job = make_job()
    env["db"].insert_job(job)
    make_worker(env, WritingAgent()).try_process(job)
    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.SUCCEEDED


def test_pr_job_uses_pr_head_branch(worker_env, tmp_path):
    env = worker_env
    seed_branch(env["origin"], "feature/feedback", tmp_path)
    env["github"].prs = [pull_request(number=7, head_ref="feature/feedback", head_clone_url=env["origin"])]
    job = make_job(
        type="pull_comment",
        pull_number=7,
        issue_number=None,
        branch="feature/feedback",
        comment_id=42,
        context={"pr_url": "https://github.com/owner/repo1/pull/7"},
    )
    env["db"].insert_job(job)
    make_worker(env, WritingAgent()).try_process(job)

    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.SUCCEEDED
    assert done.branch == "feature/feedback"
    assert "agent: address PR #7 feedback" in origin_head(env["origin"])
    _, page_num, body = env["github"].posted[-1]
    assert page_num == 7


def test_concurrent_jobs_same_repo_do_not_corrupt(worker_env):
    env = worker_env
    worker = make_worker(env, SlowJobAwareWritingAgent(delay=0.2))
    jobs = [make_job(id=new_job_id(), issue_number=n, request_text=f"@agent task {n}") for n in (1, 2)]
    for j in jobs:
        env["db"].insert_job(j)

    stop = threading.Event()
    results: list = []
    errors: list = []

    def pump():
        while not stop.is_set():
            try:
                job = env["db"].next_runnable_job()
                if job is None:
                    return
                results.append(worker.try_process(job))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
                return

    threads = [threading.Thread(target=pump) for _ in range(4)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and (env["db"].next_runnable_job() is not None):
        time.sleep(0.05)
    stop.set()
    for t in threads:
        t.join()

    assert not errors
    for j in jobs:
        assert env["db"].get_job(j.id).status == JobStatus.SUCCEEDED


def test_interrupted_running_job_recovered_on_restart(worker_env):
    env = worker_env
    job = make_job(id="running-job", status=JobStatus.RUNNING)
    env["db"].insert_job(job)
    db2 = Database(env["db"].path)
    count = db2.reset_interrupted()
    assert count == 1
    assert db2.get_job("running-job").status == JobStatus.INTERRUPTED
    db2.close()


def test_feedback_deduped(worker_env):
    env = worker_env
    job = make_job()
    env["db"].insert_job(job)
    worker = make_worker(env, WritingAgent())
    worker.try_process(job)
    n = len(env["github"].posted)
    worker.post_feedback(job)
    assert len(env["github"].posted) == n


def test_auto_create_pr(worker_env):
    env = worker_env
    env["config"].auto_create_pr = True
    job = make_job()
    env["db"].insert_job(job)
    make_worker(env, WritingAgent()).try_process(job)
    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.SUCCEEDED
    assert done.pr_url is not None
    assert env["github"].pull_requests_created
    _, head, base, _title = env["github"].pull_requests_created[0]
    assert head == "agent/issue-5"
    assert base == "main"


def test_validation_commands_never_from_github_input(worker_env):
    """The worker must only execute configured commands, never job text."""
    env = worker_env
    # Craft a malicious "request" that would be terrible if executed as a shell command.
    env["config"].validation_commands = [f'{sys.executable} -c "raise SystemExit(1)"']
    job = make_job(request_text="@agent fix this; pwn --delete-everything")
    env["db"].insert_job(job)
    make_worker(env, WritingAgent()).try_process(job)
    done = env["db"].get_job(job.id)
    assert done.status == JobStatus.FAILED  # validation failed; nothing executed from text
