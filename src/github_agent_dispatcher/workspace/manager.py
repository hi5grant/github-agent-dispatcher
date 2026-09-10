from __future__ import annotations

from pathlib import Path


class WorkspaceResolutionError(Exception):
    pass


def repository_dir(repo: str) -> str:
    """Normalize ``owner/repo`` to its nested directory path ``owner/repo``.

    Checkouts live at ``<workspace_root>/<owner>/<repo>`` — the conventional
    ``owner/repo`` layout (e.g. ``~/Codebase/hi5grant/beway-villa``). Keeping
    the nested layout means the dispatcher reuses the same checkouts the
    repositories already live in, instead of flattened ``owner--repo`` copies.
    """
    parts = repo.strip().strip("/").split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise WorkspaceResolutionError(f"invalid repository name {repo!r}")
    if parts[0] in (".", "..") or parts[1] in (".", ".."):
        raise WorkspaceResolutionError(f"invalid repository name {repo!r}")
    return f"{parts[0]}/{parts[1]}"


class WorkspaceManager:
    """Resolves repository checkouts safely beneath ``root``."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    def resolve(self, repo: str) -> Path:
        dir_name = repository_dir(repo)
        candidate = self.root / dir_name
        self._validate_under_root(candidate)
        return candidate

    def _validate_under_root(self, candidate: Path) -> Path:
        try:
            candidate.resolve().relative_to(self.root.resolve())
        except ValueError:
            raise WorkspaceResolutionError(
                f"resolved path {candidate} escapes workspace root {self.root}"
            ) from None
        return candidate

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root
