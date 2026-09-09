from __future__ import annotations

import logging

from github_agent_dispatcher.config import AppConfig
from github_agent_dispatcher.github.client import GitHubClient, RepoInfo


def resolve_repositories(config: AppConfig, client: GitHubClient) -> list[RepoInfo]:
    """Determine which repositories should be monitored.

    ``configured`` mode monitors exactly the repositories listed in
    ``GITHUB_ALLOWED_REPOS``.

    ``owned`` mode discovers repositories the token user owns (``affiliation=owner``
    only, which excludes organization repositories) and intersects that set with
    ``GITHUB_ALLOWED_REPOS`` when one is configured, so the allowlist is always
    honored.
    """
    allowed = [r.lower().strip().strip("/") for r in config.allowed_repos if r.strip()]

    if config.repository_discovery == "owned":
        owned = {(r.owner, r.name) for r in client.list_owned_repositories()}
        if not allowed:
            found = [r for r in client.list_owned_repositories() if (r.owner, r.name) in owned]
        else:
            found = [
                r
                for r in client.list_owned_repositories()
                if (r.owner, r.name) in owned and f"{r.owner}/{r.name}".lower() in allowed
            ]
    else:
        found = []
        for repo in allowed:
            try:
                found.append(client.get_repository(repo))
            except Exception as exc:  # pragma: no cover - surfaced by caller
                logging.getLogger(__name__).warning("[repos] could not resolve %s: %s", repo, exc)

    return sorted(found, key=lambda r: f"{r.owner}/{r.name}".lower())
