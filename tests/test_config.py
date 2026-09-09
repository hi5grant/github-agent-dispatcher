from __future__ import annotations

import pytest

from github_agent_dispatcher.config import ConfigError, load_config, validate_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in [
        "GITHUB_TOKEN",
        "GITHUB_ALLOWED_REPOS",
        "GITHUB_ALLOWED_USERS",
        "AGENT_TRIGGER",
        "AGENT_COMMAND",
        "WORKSPACE_ROOT",
        "POLL_INTERVAL_SECONDS",
        "AUTO_CLONE",
        "AUTO_CREATE_PR",
        "DATABASE_PATH",
        "MAX_CONCURRENT_JOBS",
        "REPOSITORY_DISCOVERY",
        "ISSUE_DISCOVERY",
        "VALIDATION_COMMANDS",
        "GAD_DATA_DIR",
        "GAD_CONFIG_FILE",
        "GAD_ENV_FILE",
        "GITHUB_ALLOW_SELF",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GAD_ENV_FILE", "/nonexistent/not-a-real-env-file")


def test_defaults(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b, c/d")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "s.db"))
    cfg = load_config()
    assert cfg.token == "tok"
    assert cfg.allowed_repos == ["a/b", "c/d"]
    assert cfg.trigger == "@agent"
    assert cfg.agent_command == "opencode run"
    assert cfg.poll_interval_seconds == 300
    assert cfg.auto_clone is True
    assert cfg.auto_create_pr is False
    assert cfg.max_concurrent_jobs == 2
    assert cfg.repository_discovery == "configured"
    assert cfg.issue_discovery == "mention"
    assert cfg.workspace_root == tmp_path / "ws"


def test_env_overrides(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("AUTO_CREATE_PR", "true")
    monkeypatch.setenv("AGENT_COMMAND", "opencode run --model qwen3")
    monkeypatch.setenv("ISSUE_DISCOVERY", "mention,label:agent")
    monkeypatch.setenv("VALIDATION_COMMANDS", "pytest,ruff check .")
    cfg = load_config()
    assert cfg.poll_interval_seconds == 30
    assert cfg.auto_create_pr is True
    assert cfg.agent_command == "opencode run --model qwen3"
    assert cfg.issue_discovery == "mention,label:agent"
    assert cfg.validation_commands == ["pytest", "ruff check ."]


def test_bad_int_raises(clean_env, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "abc")
    with pytest.raises(ConfigError):
        load_config()


def test_bad_discovery_mode_raises(clean_env, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("REPOSITORY_DISCOVERY", "whatever")
    with pytest.raises(ConfigError):
        load_config()


def test_bad_issue_mode_raises(clean_env, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("ISSUE_DISCOVERY", "bogus")
    with pytest.raises(ConfigError):
        load_config()


def test_validate_config_missing_token(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    cfg = load_config()
    problems = validate_config(cfg)
    assert any("GITHUB_TOKEN" in p for p in problems)


def test_validate_config_missing_repos(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    cfg = load_config()
    problems = validate_config(cfg)
    assert any("GITHUB_ALLOWED_REPOS" in p for p in problems)


def test_repo_overrides_from_json(clean_env, monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        '{"repositories": {"a/b": {"validation_commands": ["go test ./..."], '
        '"agent_command": "opencode run --agent plan"}}}'
    )
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("GAD_CONFIG_FILE", str(cfg_file))
    cfg = load_config()
    assert cfg.commands_for("a/b") == ["go test ./..."]
    assert cfg.agent_command_for("a/b") == "opencode run --agent plan"
    assert cfg.commands_for("c/d") == []
    assert cfg.agent_command_for("c/d") == "opencode run"


def test_per_repo_validation_falls_back(clean_env, monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"repositories": {"a/b": {}}}')
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_ALLOWED_REPOS", "a/b")
    monkeypatch.setenv("VALIDATION_COMMANDS", "pytest")
    monkeypatch.setenv("GAD_CONFIG_FILE", str(cfg_file))
    cfg = load_config()
    assert cfg.commands_for("a/b") == ["pytest"]
