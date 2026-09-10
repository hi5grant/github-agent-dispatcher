from __future__ import annotations

from pathlib import Path

import pytest

from github_agent_dispatcher.workspace.manager import (
    WorkspaceManager,
    WorkspaceResolutionError,
    repository_dir,
)


def test_repository_dir():
    assert repository_dir("owner/repo") == "owner/repo"
    assert repository_dir("OWNER/Repo") == "OWNER/Repo"
    assert repository_dir("  a/b  ") == "a/b"


def test_repository_dir_invalid():
    with pytest.raises(WorkspaceResolutionError):
        repository_dir("notajotapairedname")
    with pytest.raises(WorkspaceResolutionError):
        repository_dir("a/b/c")


def test_resolve_within_root(tmp_path: Path):
    man = WorkspaceManager(tmp_path)
    p = man.resolve("owner/repo")
    assert p == tmp_path / "owner" / "repo"
    assert str(p).startswith(str(tmp_path))


def test_macos_style_path(tmp_path: Path):
    root = tmp_path / "Codebase"
    man = WorkspaceManager(root)
    p = man.resolve("grantlindsey/beway-villa")
    assert p == root / "grantlindsey" / "beway-villa"
    assert root in p.parents


def test_windows_style_path(tmp_path: Path):
    man = WorkspaceManager(tmp_path)
    p = man.resolve("owner/repo")
    assert p.parent == tmp_path / "owner"


def test_traversal_attempt_is_blocked(tmp_path: Path):
    man = WorkspaceManager(tmp_path)
    # Repository names must be exactly two safe components; embedded separators
    # cannot escape the workspace root.
    with pytest.raises(WorkspaceResolutionError):
        repository_dir("owner/../rebcode")
    with pytest.raises(WorkspaceResolutionError):
        repository_dir("owner/..")
    # A hostile name still cannot escape the root; it is rejected outright.
    with pytest.raises(WorkspaceResolutionError):
        man.resolve("../escape")
    with pytest.raises(WorkspaceResolutionError):
        man.resolve("escape/..")


def test_resolve_rejects_non_two_part_names(tmp_path: Path):
    man = WorkspaceManager(tmp_path)
    with pytest.raises(WorkspaceResolutionError):
        man.resolve("onlyone")


def test_ensure_root_creates(tmp_path: Path):
    root = tmp_path / "nested" / "root"
    man = WorkspaceManager(root)
    created = man.ensure_root()
    assert created.exists()
    assert created.is_dir()
