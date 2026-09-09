from __future__ import annotations

import re


class Authorization:
    """Decide which users and repositories may trigger agent work.

    Repository authorization is always an allowlist check against
    ``GITHUB_ALLOWED_REPOS``.  User authorization checks ``GITHUB_ALLOWED_USERS``,
    plus the token owner when ``allow_self`` is enabled (the default).
    """

    def __init__(
        self,
        allowed_repos: list[str] | None = None,
        allowed_users: list[str] | None = None,
        token_owner: str | None = None,
        allow_self: bool = True,
    ):
        self.allowed_repos = {r.lower().strip().strip("/") for r in (allowed_repos or []) if r.strip()}
        self.allowed_users = {u.strip().lower() for u in (allowed_users or []) if u.strip()}
        self.token_owner = token_owner.lower() if token_owner else None
        self.allow_self = allow_self

    def repo_allowed(self, repo: str) -> bool:
        return repo.lower().strip().strip("/") in self.allowed_repos

    def user_allowed(self, login: str) -> bool:
        if not login:
            return False
        if login.lower() in self.allowed_users:
            return True
        if self.allow_self and self.token_owner and login.lower() == self.token_owner:
            return True
        return False


def contains_trigger(text: str, trigger: str) -> bool:
    """True if ``trigger`` appears in ``text`` as a whole token.

    ``@agent`` matches ``@agent try this`` but not ``@agents``.
    """
    if not trigger:
        return False
    pattern = re.compile(r"(?<!\w)" + re.escape(trigger) + r"(?!\w)", re.IGNORECASE)
    return pattern.search(text or "") is not None
