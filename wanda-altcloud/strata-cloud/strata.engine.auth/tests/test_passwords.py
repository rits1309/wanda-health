"""The local pool-policy mirror (core/passwords.py,). The mirror must
be no laxer than the real Cognito pool: anything it passes that the pool then
rejects becomes a confusing drift 400 dead-end for a user who satisfied the
advertised policy (review finding), so these pin the edges Cognito enforces —
ASCII-only character classes, no leading/trailing whitespace, the 256 ceiling.
"""

from strata_engine_auth.core.config import Settings
from strata_engine_auth.core.passwords import COGNITO_MAX_LENGTH, validate_password_policy

_SETTINGS = Settings(
    cognito_region="eu-west-2",
    cognito_user_pool_id="eu-west-2_test",
    cognito_client_id="testclient",
    database_url="postgresql+asyncpg://x/y",
)


def test_a_compliant_password_is_accepted() -> None:
    """A password meeting every class with an internal symbol is compliant."""
    assert validate_password_policy("Str0ng!pass", _SETTINGS) == []


def test_an_internal_space_counts_as_the_symbol() -> None:
    """Cognito counts a non-edge space as a special character; so does the mirror."""
    assert validate_password_policy("Str0ng pass", _SETTINGS) == []


def test_a_trailing_space_is_rejected_not_counted_as_a_symbol() -> None:
    """A password whose only 'symbol' is a trailing space must be rejected — Cognito
    refuses leading/trailing whitespace, so counting it would pass locally then trip
    the pool's drift backstop (the F1/C4 dead-end)."""
    assert "whitespace" in validate_password_policy("Str0ngpass ", _SETTINGS)


def test_a_leading_space_is_rejected() -> None:
    """Leading whitespace is refused just like trailing."""
    assert "whitespace" in validate_password_policy(" Str0ng!pass", _SETTINGS)


def test_non_ascii_uppercase_does_not_satisfy_the_uppercase_class() -> None:
    """Cognito's uppercase class is A-Z; a non-ASCII uppercase letter must not count,
    or the mirror passes a password the pool rejects."""
    assert "uppercase" in validate_password_policy("émint0!pass", _SETTINGS)


def test_non_ascii_digit_does_not_satisfy_the_digit_class() -> None:
    """Cognito's digit class is 0-9; an Arabic-Indic digit must not count."""
    assert "digit" in validate_password_policy("Strong!pass٣", _SETTINGS)


def test_over_the_ceiling_is_rejected() -> None:
    """A password past Cognito's 256 ceiling gets the upgrade signal, not a drift 400."""
    assert "max_length" in validate_password_policy("Aa1!" + "x" * COGNITO_MAX_LENGTH, _SETTINGS)
