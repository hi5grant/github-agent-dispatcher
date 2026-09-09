from __future__ import annotations

from pathlib import Path

from github_agent_dispatcher.agents.base import build_prompt
from github_agent_dispatcher.agents.opencode import OpenCodeBackend
from github_agent_dispatcher.jobs.models import Job, JobStatus, new_job_id


def make_job(**kwargs):
    defaults = dict(
        id=new_job_id(),
        type="issue",
        repo="owner/repo1",
        issue_number=7,
        pull_number=None,
        branch=None,
        comment_id=None,
        requester="owner",
        request_text="@agent please fix",
        status=JobStatus.QUEUED,
        created_at=Job.now(),
        context={"issue_url": "https://github.com/owner/repo1/issues/7"},
    )
    defaults.update(kwargs)
    return Job(**defaults)


def test_build_prompt_contains_context_and_rules(tmp_path: Path):
    job = make_job()
    prompt = build_prompt(job)
    assert "owner/repo1" in prompt
    assert "@agent please fix" in prompt
    assert "Do not commit and do not push" in prompt
    assert "Inspect the repository" in prompt
    assert "https://api.github.com/repos/owner/repo1/issues/7/comments" in prompt
    assert "🤖" in prompt
    assert "PASS" in prompt
    assert "FAIL" in prompt


def test_opencode_invokes_subprocess_success(tmp_path: Path, monkeypatch):
    # Simulate ambient side effect that the test can observe: write a file.
    marker = tmp_path / "was-run.txt"

    import github_agent_dispatcher.agents.opencode as mod

    class FakeProc:
        returncode = 0
        stdout = "I implemented it"
        stderr = ""

    captured = {}

    def fake_run(argv, cwd=None, capture_output=True, text=True, timeout=None):
        captured["argv"] = argv
        captured["cwd"] = cwd
        marker.write_text("ran")
        return FakeProc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    backend = OpenCodeBackend("opencode run")
    job = make_job()
    result = backend.run(job.request_text, {"_job": job}, tmp_path)
    assert result.success is True
    assert captured["argv"][0:2] == ["opencode", "run"]
    assert "Do not commit" in captured["argv"][-1]
    assert captured["cwd"] == str(tmp_path)
    assert marker.exists()


def test_opencode_nonzero_exit_is_failure(tmp_path: Path, monkeypatch):
    import github_agent_dispatcher.agents.opencode as mod

    class FakeProc:
        returncode = 3
        stdout = ""
        stderr = "kaboom"

    def fake_run(argv, cwd=None, capture_output=True, text=True, timeout=None):
        return FakeProc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    backend = OpenCodeBackend("opencode run")
    result = backend.run("@agent x", {"_job": make_job()}, tmp_path)
    assert result.success is False
    assert "exit code 3" in result.error


def test_opencode_missing_executable(tmp_path: Path, monkeypatch):
    import github_agent_dispatcher.agents.opencode as mod

    def fake_run(argv, cwd=None, capture_output=True, text=True, timeout=None):
        raise FileNotFoundError("no such binary")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    backend = OpenCodeBackend("definitely-not-a-real-binary-xyz run")
    result = backend.run("@agent x", {"_job": make_job()}, tmp_path)
    assert result.success is False
    assert "not found" in result.error


def test_custom_command_and_extra_args(tmp_path: Path, monkeypatch):
    import github_agent_dispatcher.agents.opencode as mod

    captured = {}

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, cwd=None, capture_output=True, text=True, timeout=None):
        captured["argv"] = argv
        return FakeProc()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    backend = OpenCodeBackend("my-agent run --flag", extra_args=["--model", "claude"])
    result = backend.run("@agent x", {"_job": make_job()}, tmp_path)
    assert result.success is True
    assert captured["argv"][:5] == ["my-agent", "run", "--flag", "--model", "claude"]
    assert "Do not commit" in captured["argv"][-1]
