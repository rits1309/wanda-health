"""provision_users.py's one safety-critical property: it never runs against prod."""

import pytest

from scripts.provision_users import USERS, ROLE_NAMES, _guard
from strata_engine_auth.core.config import settings


def test_guard_refuses_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "prod")
    with pytest.raises(SystemExit, match="Refusing to run against prod"):
        _guard()


@pytest.mark.parametrize("environment", ["local", "develop", "uat"])
def test_guard_allows_non_prod(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setattr(settings, "environment", environment)
    _guard()  # must not raise


def test_every_checked_in_user_has_a_catalogue_role() -> None:
    for user in USERS:
        assert user.role in ROLE_NAMES, f"{user.email} has unknown role {user.role!r}"


def test_checked_in_emails_are_unique() -> None:
    emails = [user.email for user in USERS]
    assert len(set(emails)) == len(emails)
