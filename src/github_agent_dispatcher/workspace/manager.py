from __future__ import annotations

from pathlib import Path


class WorkspaceResolutionError(Exception):
    pass


def repository_slug(repo: str) -> str:
    """Normalize ``owner/repo`` to the single directory component ``owner--repo``.

    Using ``owner--repo`` avoids both directory collisions and any ambiguity
    from slashes embedded in names.
    """
    parts = repo.strip().strip("/").split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise WorkspaceResolutionError(f"invalid repository name {repo!r}")
    return f"{parts[0]}--{parts[1]}"


class WorkspaceManager:
    """Resolves repository checkouts safely beneath ``root``."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()

    def resolve(self, repo: str) -> Path:
        dir_name = repository_slug(repo)
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
