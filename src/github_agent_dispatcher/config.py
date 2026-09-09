from __future__ import annotations

import json
import logging
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


def _env(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    return default if value is None or not value.strip() else value.strip()


def _env_bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value.strip())
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got {value!r}") from None


def _env_list(key: str) -> list[str]:
    value = os.environ.get(key)
    if not value:
        return []
    parts = []
    for raw in value.replace("\n", ",").split(","):
        item = raw.strip()
        if item:
            parts.append(item)
    return parts


def _split_command(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError:
        return value.split()


def default_data_dir() -> Path:
    return Path.home() / ".github-agent-dispatcher"


@dataclass
class RepositoryOverride:
    validation_commands: list[str] = field(default_factory=list)
    agent_command: str | None = None


@dataclass
class AppConfig:
    token: str
    api_url: str
    github_web_url: str
    allowed_repos: list[str]
    allowed_users: list[str]
    allow_self: bool
    trigger: str
    agent_command: str
    agent_extra_args: list[str]
    workspace_root: Path
    poll_interval_seconds: int
    auto_clone: bool
    auto_create_pr: bool
    database_path: Path
    max_concurrent_jobs: int
    repository_discovery: str
    issue_discovery: str
    validation_commands: list[str]
    data_dir: Path
    log_level: str
    token_owner: str | None = None
    repo_overrides: dict[str, RepositoryOverride] = field(default_factory=dict)

    def commands_for(self, repo: str) -> list[str]:
        override = self.repo_overrides.get(repo)
        if override and override.validation_commands:
            return list(override.validation_commands)
        return list(self.validation_commands)

    def agent_command_for(self, repo: str) -> str | None:
        override = self.repo_overrides.get(repo)
        if override and override.agent_command:
            return override.agent_command
        return self.agent_command


def _load_repo_overrides(config_file: Path) -> dict[str, RepositoryOverride]:
    if not config_file.exists():
        return {}
    try:
        raw = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not parse config file {config_file}: {exc}") from exc
    overrides: dict[str, RepositoryOverride] = {}
    repos = raw.get("repositories", {})
    for repo, settings in repos.items():
        if not isinstance(settings, dict):
            continue
        validation = settings.get("validation_commands", [])
        overrides[repo] = RepositoryOverride(
            validation_commands=[str(c) for c in validation] if isinstance(validation, list) else [],
            agent_command=settings.get("agent_command"),
        )
    return overrides


def load_config(env_file: str | None = None) -> AppConfig:
    dotenv_path = env_file or os.environ.get("GAD_ENV_FILE") or str(Path.cwd() / ".env")
    if Path(dotenv_path).exists():
        load_dotenv(dotenv_path, override=False)

    data_dir = Path(_env("GAD_DATA_DIR", str(default_data_dir()))).expanduser()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("could not create data dir %s: %s", data_dir, exc)

    config_file = Path(_env("GAD_CONFIG_FILE", str(data_dir / "config.json"))).expanduser()
    repo_overrides = _load_repo_overrides(config_file)

    database_path = Path(_env("DATABASE_PATH", str(data_dir / "state.db"))).expanduser()
    workspace_root = Path(_env("WORKSPACE_ROOT", str(Path.home() / "Codebase"))).expanduser()

    token = _env("GITHUB_TOKEN")
    api_url = _env("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    web_url = _env("GITHUB_WEB_URL", "https://github.com").rstrip("/")

    allowed_repos = _env_list("GITHUB_ALLOWED_REPOS")
    allowed_users = _env_list("GITHUB_ALLOWED_USERS")

    discovery = _env("REPOSITORY_DISCOVERY", "configured").lower()
    if discovery not in {"configured", "owned"}:
        raise ConfigError("REPOSITORY_DISCOVERY must be 'configured' or 'owned'")

    issue_discovery = (
        _env(
            "ISSUE_DISCOVERY",
            "mention",
        )
        .lower()
        .replace(" ", "")
    )
    valid_issue_modes = {"mention", "assigned", "label:agent", "none", "any"}
    for mode in [m for m in issue_discovery.split(",") if m]:
        if mode not in valid_issue_modes:
            raise ConfigError(
                f"ISSUE_DISCOVERY contains invalid mode {mode!r}; "
                f"valid modes: {', '.join(sorted(valid_issue_modes))}"
            )

    validation_commands = [c.strip() for c in _env_list("VALIDATION_COMMANDS") if c.strip()]

    return AppConfig(
        token=token,
        api_url=api_url,
        github_web_url=web_url,
        allowed_repos=allowed_repos,
        allowed_users=allowed_users,
        allow_self=_env_bool("GITHUB_ALLOW_SELF", True),
        trigger=_env("AGENT_TRIGGER", "@agent"),
        agent_command=_env("AGENT_COMMAND", "opencode run"),
        agent_extra_args=_env_list("AGENT_EXTRA_ARGS"),
        workspace_root=workspace_root,
        poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 300),
        auto_clone=_env_bool("AUTO_CLONE", True),
        auto_create_pr=_env_bool("AUTO_CREATE_PR", False),
        database_path=database_path,
        max_concurrent_jobs=_env_int("MAX_CONCURRENT_JOBS", 2),
        repository_discovery=discovery,
        issue_discovery=issue_discovery,
        validation_commands=validation_commands,
        data_dir=data_dir,
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        repo_overrides=repo_overrides,
    )


def validate_config(config: AppConfig) -> list[str]:
    problems: list[str] = []
    if not config.token:
        problems.append("GITHUB_TOKEN is not set")
    if config.repository_discovery == "configured" and not config.allowed_repos:
        problems.append("REPOSITORY_DISCOVERY=configured but GITHUB_ALLOWED_REPOS is empty")
    if not config.trigger:
        problems.append("AGENT_TRIGGER must not be empty")
    if config.poll_interval_seconds < 10:
        problems.append("POLL_INTERVAL_SECONDS is very low; minimum sensible value is 10")
    try:
        config.database_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        problems.append(f"cannot create database directory {config.database_path.parent}: {exc}")
    try:
        config.workspace_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        problems.append(f"cannot create workspace root {config.workspace_root}: {exc}")
    return problems
