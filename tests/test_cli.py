from __future__ import annotations

from github_agent_dispatcher import __version__
from github_agent_dispatcher.cli import main


def test_version_flag(capsys):
    with __import__("pytest").raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_help_available(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for command in ["serve", "scan", "doctor", "status", "repos", "jobs", "retry", "cancel"]:
        assert command in out


def test_default_no_token_serve_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "s.db"))
    code = main(["serve", "--interval", "1"])
    assert code == 1


def test_doctor_outputs_checks(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "s.db"))
    code = main(["doctor"])
    out = capsys.readouterr().out
    assert code != 0
    assert "github-token" in out
    assert "workspace" in out
    assert "agent-backend" in out


def test_jobs_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "s.db"))
    assert main(["jobs"]) == 0
