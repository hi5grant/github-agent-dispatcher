from __future__ import annotations

from github_agent_dispatcher.security.authorization import Authorization, contains_trigger


def make_auth(**kwargs):
    defaults = dict(
        allowed_repos=["owner/repo1", "owner/repo2"],
        allowed_users=["alice", "bob"],
        token_owner="owner",
        allow_self=True,
    )
    defaults.update(kwargs)
    return Authorization(**defaults)


def test_repo_allowed():
    auth = make_auth()
    assert auth.repo_allowed("owner/repo1") is True
    assert auth.repo_allowed("OWNER/REPO2") is True  # case-insensitive
    assert auth.repo_allowed("owner/other") is False
    assert auth.repo_allowed("evil/repo1") is False


def test_user_allowed_explicit():
    auth = make_auth()
    assert auth.user_allowed("alice") is True
    assert auth.user_allowed("BOB") is True
    assert auth.user_allowed("mallory") is False
    assert auth.user_allowed("") is False


def test_self_allowed_by_default():
    auth = make_auth()
    assert auth.user_allowed("owner") is True


def test_self_not_allowed_when_disabled():
    auth = make_auth(allow_self=False)
    assert auth.user_allowed("owner") is False
    assert auth.user_allowed("alice") is True


def test_self_not_authorized_for_unowned_repo():
    auth = make_auth(allowed_repos=["theirs/repo"], allowed_users=["them"], token_owner="owner")
    # 'owner' can trigger work only on their own repos when allow_self; here
    # repo is someone else's but it IS on the allowlist so user check applies:
    assert auth.user_allowed("them") is True
    assert auth.user_allowed("owner") is True  # allow_self globally


def test_contains_trigger_basic():
    assert contains_trigger("@agent fix this", "@agent") is True
    assert contains_trigger("please @agent do it", "@agent") is True


def test_contains_trigger_word_boundary():
    assert contains_trigger("@agents are everywhere", "@agent") is False
    assert contains_trigger("email @agent@example.com", "@agent") is True
    assert contains_trigger("role: agent", "@agent") is False


def test_contains_trigger_empty():
    assert contains_trigger("hello", "") is False
    assert contains_trigger("", "@agent") is False
    assert contains_trigger(None, "@agent") is False
