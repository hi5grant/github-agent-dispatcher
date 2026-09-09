from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from github_agent_dispatcher.git.lock import RepoLock
from github_agent_dispatcher.git.repository import Repository


def git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True, check=True)


def make_clone(tmp_path: Path, origin: str, identity) -> Path:
    work = tmp_path / "work"
    subprocess.run(["git", "clone", origin, str(work)], check=True, capture_output=True)
    identity(work)
    return work


@pytest.fixture
def origin_and_clone(tmp_path, do_git, git_identity):
    origin = do_git()
    work = make_clone(tmp_path, origin, git_identity)
    return origin, work


def test_clone_and_status(origin_and_clone):
    origin, work = origin_and_clone
    repo = Repository(work)
    assert repo.exists() is True
    assert repo.current_branch() == "main"
    assert repo.is_dirty() is False


def test_dirty_detection(tmp_path, do_git, git_identity):
    origin = do_git()
    work = make_clone(tmp_path, origin, git_identity)
    repo = Repository(work)
    (work / "file.txt").write_text("hello")
    assert repo.is_dirty() is True
    assert repo.status_porcelain() != ""


def test_untracked_only_counts_as_dirty(tmp_path, do_git, git_identity):
    origin = do_git()
    work = make_clone(tmp_path, origin, git_identity)
    repo = Repository(work)
    (work / "new-file.txt").write_text("x")
    assert repo.is_dirty() is True
    assert repo.has_untracked() is True


def test_ensure_clean_for_work_blocks_dirty(tmp_path, do_git, git_identity):
    origin = do_git()
    work = make_clone(tmp_path, origin, git_identity)
    repo = Repository(work)
    (work / "file.txt").write_text("hello")
    ok, message = repo.ensure_clean_for_work()
    assert ok is False
    assert "pre-existing" in message


def test_commit_and_push(origin_and_clone):
    origin, work = origin_and_clone
    repo = Repository(work)
    (work / "file.txt").write_text("content\n")
    repo.stage_all()
    sha = repo.commit("initial commit")
    assert len(sha) == 40
    repo.push("origin", "main")
    # verify the bare origin now contains the commit
    proc = subprocess.run(
        ["git", "-C", origin, "log", "--oneline", "-1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert sha[:7] in proc.stdout or sha in proc.stdout


def test_checkout_create_branch(origin_and_clone):
    _, work = origin_and_clone
    repo = Repository(work)
    repo.checkout("agent/issue-1", create=True)
    assert repo.current_branch() == "agent/issue-1"


def test_ensure_remote(origin_and_clone):
    origin, work = origin_and_clone
    repo = Repository(work)
    repo.ensure_remote("origin", origin)
    assert repo.origin_url() == origin
    repo.ensure_remote("extra", origin)
    proc = subprocess.run(["git", "remote"], cwd=str(work), capture_output=True, text=True, check=True)
    assert "extra" in proc.stdout.split()


def test_fetch_pr_branch(origin_and_clone, git_identity):
    origin, work = origin_and_clone
    repo = Repository(work)
    # create a branch in origin
    repo.checkout("feature/topic", create=True)
    (work / "topic.txt").write_text("t\n")
    repo.stage_all()
    repo.commit("topic commit")
    repo.push("origin", "feature/topic")

    repo.checkout("main")
    # simulate the worker: delete any local copy, then re-checkout from remote
    subprocess.run(["git", "branch", "-D", "feature/topic"], cwd=work, check=True, capture_output=True)
    repo.ensure_remote("origin", origin)
    repo.fetch("origin", force=True)
    repo.checkout("feature/topic", create=True, start_point="origin/feature/topic")
    assert repo.current_branch() == "feature/topic"


def test_dirty_workspace_protected_against_reset(tmp_path, do_git, git_identity):
    origin = do_git()
    work = make_clone(tmp_path, origin, git_identity)
    repo = Repository(work)
    (work / "precious.txt").write_text("MY LOCAL WORK")
    ok, _ = repo.ensure_clean_for_work()
    assert ok is False
    # The uncommitted file must still exist untouched.
    assert (work / "precious.txt").read_text() == "MY LOCAL WORK"


# ----------------------------------------------------------------- locking
def test_file_lock_exclusive(tmp_path: Path):
    lock = RepoLock(tmp_path / "repo.lock")
    assert lock.acquire(timeout=0.1) is True
    other = RepoLock(tmp_path / "repo.lock")
    assert other.acquire(timeout=0.1) is False
    lock.release()
    assert other.acquire(timeout=0.2) is True
    other.release()


def test_lock_release_releases_resources(tmp_path: Path):
    lock = RepoLock(tmp_path / "r.lock")
    lock.acquire(timeout=0.1)
    lock.release()
    assert not (tmp_path / "r.lock").exists()


def test_stale_lock_removed(tmp_path: Path):
    lock_file = tmp_path / "stale.lock"
    with open(lock_file, "w") as f:
        f.write("999999999")  # PID that does not exist
    lock = RepoLock(lock_file)
    assert lock.acquire(timeout=0.5) is True
    lock.release()
