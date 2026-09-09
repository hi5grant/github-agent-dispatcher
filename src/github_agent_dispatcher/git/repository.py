from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class GitError(Exception):
    def __init__(self, message: str, command: list[str] | None = None):
        super().__init__(message)
        self.command = command


class DirtyWorkspaceError(GitError):
    pass


def _run(
    args: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    check: bool = True,
    text: bool = True,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=text,
        env=env,
    )
    if check and proc.returncode != 0:
        err = (proc.stderr or "").strip() or (proc.stdout or "").strip()
        raise GitError(f"git {' '.join(args)} failed: {err}", args)
    return proc


class Repository:
    def __init__(self, path: Path):
        self.path = path

    # ------------------------------------------------------------- existence
    def exists(self) -> bool:
        return (self.path / ".git").exists()

    def clone(self, clone_url: str, branch: str | None = None, depth: int | None = 1) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        args = ["git", "clone"]
        if branch:
            args += ["--branch", branch]
        if depth:
            args += ["--depth", str(depth)]
        args += [clone_url, str(self.path)]
        _run(args)

    def remotes(self) -> list[str]:
        proc = _run(["git", "remote"], cwd=self.path)
        return [line for line in proc.stdout.splitlines() if line.strip()]

    def current_branch(self) -> str | None:
        proc = _run(["git", "symbolic-ref", "--short", "HEAD"], cwd=self.path, check=False)
        if proc.returncode == 0:
            return proc.stdout.strip()
        return None

    def origin_url(self) -> str | None:
        proc = _run(["git", "config", "--get", "remote.origin.url"], cwd=self.path, check=False)
        if proc.returncode == 0:
            return proc.stdout.strip()
        return None

    # -------------------------------------------------------------- fetching
    def fetch(self, remote: str = "origin", force: bool = False, prune: bool = True) -> None:
        args = ["git", "fetch", remote] + (["--prune"] if prune else []) + (["--force"] if force else [])
        _run(args, cwd=self.path)

    def reset_hard(self, ref: str = "HEAD") -> None:
        _run(["git", "reset", "--hard", ref], cwd=self.path)

    def checkout(self, branch: str, create: bool = False, start_point: str | None = None) -> None:
        args = ["git", "checkout"] + (["-b"] if create else []) + [branch]
        if start_point:
            args.append(start_point)
        _run(args, cwd=self.path)

    def ensure_remote(self, name: str, url: str) -> None:
        proc = _run(["git", "remote", "get-url", name], cwd=self.path, check=False)
        if proc.returncode != 0:
            _run(["git", "remote", "add", name, url], cwd=self.path)
        else:
            _run(["git", "remote", "set-url", name, url], cwd=self.path)

    # -------------------------------------------------------------- workspace
    def status_porcelain(self) -> str:
        proc = _run(["git", "status", "--porcelain"], cwd=self.path)
        return proc.stdout

    def is_dirty(self, allow_tags: set[str] | None = None) -> bool:
        allow_tags = allow_tags or set()
        for line in self.status_porcelain().splitlines():
            code = line[:2].strip()
            if code in {"??"}:
                if "new-untracked" in allow_tags:
                    continue
                return True
            if line:
                return True
        return False

    def has_untracked(self) -> bool:
        return any(line.startswith("??") for line in self.status_porcelain().splitlines() if line)

    def diff_stat(self) -> str:
        proc = _run(["git", "diff", "--stat"], cwd=self.path)
        staged = _run(["git", "diff", "--cached", "--stat"], cwd=self.path)
        return (staged.stdout + proc.stdout).strip()

    def changed_files(self) -> list[str]:
        proc = _run(["git", "diff", "--name-only", "HEAD"], cwd=self.path)
        return [line for line in proc.stdout.splitlines() if line.strip()]

    # ------------------------------------------------------------ commit/push
    def stage_all(self) -> None:
        _run(["git", "add", "-A"], cwd=self.path)

    def commit(self, message: str, author: str | None = None) -> str:
        args = ["git", "commit", "-m", message]
        if author:
            args += ["--author", author]
        _run(args, cwd=self.path)
        proc = _run(["git", "rev-parse", "HEAD"], cwd=self.path)
        return proc.stdout.strip()

    def push(self, remote: str, branch: str, force: bool = False) -> None:
        args = ["git", "push"] + (["--force-with-lease"] if force else []) + [remote, f"HEAD:{branch}"]
        _run(args, cwd=self.path)

    def log_n(self, n: int = 20) -> str:
        proc = _run(
            ["git", "log", "--oneline", f"-{n}"],
            cwd=self.path,
            check=False,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()

    # ---------------------------------------------------------------- helpers
    def ensure_clean_for_work(self) -> tuple[bool, str]:
        """Return (ok, message). Refuses to run on a dirty checkout."""
        status = self.status_porcelain().strip()
        if status:
            return False, (
                "repository has pre-existing local changes:\n"
                + status
                + "\nResolve or stash them, then retry the job. "
                "The dispatcher never destroys local changes."
            )
        return True, "clean"
