"""provision_users_from_csv.py's CSV parsing, its create-if-missing Cognito
path, and its one safety-critical property: it never runs against prod
(mirrors test_provision_users.py)."""

import json
from typing import Any

import pytest

from scripts.provision_users_from_csv import (
    _create_user,
    _generate_password,
    _get_or_create_sub,
    _guard,
    _name_from_email,
    _new_accounts_json_line,
    _parse_users,
)
from strata_engine_auth.core.config import settings
from strata_engine_auth.core.passwords import validate_password_policy


def test_guard_refuses_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "prod")
    with pytest.raises(SystemExit, match="Refusing to run against prod"):
        _guard()


@pytest.mark.parametrize("environment", ["local", "develop", "uat"])
def test_guard_allows_non_prod(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setattr(settings, "environment", environment)
    _guard()  # must not raise


def test_parses_the_minimal_email_only_csv() -> None:
    users = _parse_users("Email Address\nabhishek.hp@altcloudai.com\n")
    assert len(users) == 1
    assert users[0].email == "abhishek.hp@altcloudai.com"
    assert users[0].role == "Coach"  # default role
    assert users[0].first_name == "Abhishek Hp"  # derived from the local part


def test_parses_optional_role_and_name_columns() -> None:
    csv_text = "Email Address,Role,First Name,Last Name\nadmin@example.com,admin,Ada,Min\n"
    users = _parse_users(csv_text)
    assert users[0].role == "Admin"
    assert users[0].first_name == "Ada"
    assert users[0].last_name == "Min"


def test_column_matching_is_case_insensitive_and_order_independent() -> None:
    csv_text = "role,email address\ncoach,user@example.com\n"
    users = _parse_users(csv_text)
    assert users[0].email == "user@example.com"


def test_blank_rows_are_skipped_not_failed() -> None:
    csv_text = "Email Address\nuser@example.com\n\n"
    users = _parse_users(csv_text)
    assert len(users) == 1


def test_an_unknown_role_is_a_named_failure() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        _parse_users("Email Address,Role\nuser@example.com,superadmin\n")


def test_an_empty_csv_is_a_named_failure_not_a_silent_no_op() -> None:
    with pytest.raises(SystemExit, match="No users found"):
        _parse_users("Email Address\n")


def test_name_from_email_derives_a_readable_fallback() -> None:
    assert _name_from_email("first.last@example.com") == "First Last"


class _UserNotFoundException(Exception):
    pass


class _FakeIdp:
    """A minimal boto3 cognito-idp stand-in -- enough surface for
    _get_or_create_sub/_create_user, no network (TESTING.md: no live AWS)."""

    class exceptions:
        UserNotFoundException = _UserNotFoundException

    def __init__(self, existing: dict[str, str] | None = None) -> None:
        self.existing = existing or {}  # email -> sub
        self.created: list[dict[str, Any]] = []
        self.passwords_set: list[dict[str, Any]] = []

    def admin_get_user(self, UserPoolId: str, Username: str) -> dict[str, Any]:
        if Username not in self.existing:
            raise self.exceptions.UserNotFoundException("not found")
        return {"UserAttributes": [{"Name": "sub", "Value": self.existing[Username]}]}

    def admin_create_user(self, **kwargs: Any) -> dict[str, Any]:
        self.created.append(kwargs)
        return {"User": {"Attributes": [{"Name": "sub", "Value": "new-sub-1"}]}}

    def admin_set_user_password(self, **kwargs: Any) -> None:
        self.passwords_set.append(kwargs)


def test_generate_password_satisfies_the_configured_policy() -> None:
    for _ in range(20):  # randomized -- run it enough times to catch an edge case
        assert validate_password_policy(_generate_password(), settings) == []


def test_get_or_create_sub_adopts_an_existing_user_without_touching_its_password() -> None:
    idp = _FakeIdp(existing={"user@example.com": "existing-sub"})
    sub, created, generated_password = _get_or_create_sub(idp, "user@example.com", None)
    assert sub == "existing-sub"
    assert created is False
    assert generated_password is None
    assert idp.created == []
    assert idp.passwords_set == []


def test_get_or_create_sub_adopts_an_existing_user_even_if_a_password_was_supplied() -> None:
    """A CSV Password column is only ever consulted for a genuinely NEW
    account -- an existing one's password is never touched, column or not."""
    idp = _FakeIdp(existing={"user@example.com": "existing-sub"})
    sub, created, generated_password = _get_or_create_sub(idp, "user@example.com", "Suppl1ed!2026")
    assert sub == "existing-sub"
    assert created is False
    assert generated_password is None
    assert idp.passwords_set == []


def test_get_or_create_sub_creates_a_missing_user_with_a_generated_password() -> None:
    idp = _FakeIdp()
    sub, created, generated_password = _get_or_create_sub(idp, "new.user@example.com", None)
    assert sub == "new-sub-1"
    assert created is True
    assert generated_password is not None
    assert validate_password_policy(generated_password, settings) == []
    assert idp.created[0]["UserAttributes"] == [
        {"Name": "email", "Value": "new.user@example.com"},
        {"Name": "email_verified", "Value": "true"},
    ]
    assert idp.created[0]["MessageAction"] == "SUPPRESS"
    assert idp.passwords_set[0]["Username"] == "new.user@example.com"
    assert idp.passwords_set[0]["Password"] == generated_password
    assert idp.passwords_set[0]["Permanent"] is True


def test_get_or_create_sub_creates_a_missing_user_with_the_supplied_password() -> None:
    idp = _FakeIdp()
    sub, created, generated_password = _get_or_create_sub(
        idp, "new.user@example.com", "Suppl1ed!2026"
    )
    assert sub == "new-sub-1"
    assert created is True
    assert generated_password is None  # already known to the caller -- nothing to surface
    assert idp.passwords_set[0]["Password"] == "Suppl1ed!2026"


def test_create_user_username_is_the_email() -> None:
    """This pool is configured with username_attributes = ["email"]
    (modules/auth/cognito/main.tf), not alias_attributes -- confirmed
    against the real develop pool: a UUID username was rejected outright
    ("Username should be an email")."""
    idp = _FakeIdp()
    _create_user(idp, "someone@example.com", None)
    assert idp.created[0]["Username"] == "someone@example.com"


def test_parses_the_optional_password_column() -> None:
    csv_text = "Email Address,Password\nuser@example.com,Compl1ant!2026\n"
    users = _parse_users(csv_text)
    assert users[0].password == "Compl1ant!2026"


def test_a_non_compliant_supplied_password_is_a_named_failure_before_any_cognito_call() -> None:
    with pytest.raises(ValueError, match="fails policy"):
        _parse_users("Email Address,Password\nuser@example.com,short\n")


def test_new_accounts_json_line_is_greppable_and_parseable() -> None:
    line = _new_accounts_json_line([("new@example.com", "Compl1ant!2026")])
    assert line.startswith("NEW_ACCOUNTS_JSON:")
    payload = json.loads(line.removeprefix("NEW_ACCOUNTS_JSON:"))
    assert payload == [{"email": "new@example.com", "password": "Compl1ant!2026"}]


def test_new_accounts_json_line_is_emitted_even_when_empty() -> None:
    """A wrapper script parsing this line must never have to guess whether
    it exists -- an empty run still gets a valid, parseable "[]"."""
    line = _new_accounts_json_line([])
    assert json.loads(line.removeprefix("NEW_ACCOUNTS_JSON:")) == []
