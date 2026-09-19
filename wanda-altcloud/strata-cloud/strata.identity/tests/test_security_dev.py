"""Dev-mode verification: mint/verify, expiry, booking-claim compatibility, the factory."""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from strata_identity.security import (
    CognitoTokenVerifier,
    DevTokenVerifier,
    create_verifier,
    mint_dev_token,
)

SECRET = "test-secret"  # noqa: S105 — test fixture value


def _in_an_hour() -> int:
    return int((datetime.now(UTC) + timedelta(hours=1)).timestamp())


def test_mint_and_verify_round_trip() -> None:
    """A minted dev token verifies back to its subject, sorted roles and raw token."""
    token = mint_dev_token(SECRET, sub="u-1", roles=["Coach", "Admin"])
    principal = DevTokenVerifier(SECRET).verify(token)
    assert principal.sub == "u-1"
    assert principal.roles == ["Admin", "Coach"]
    assert principal.token == token


def test_expired_token_is_rejected() -> None:
    """An expired dev token is rejected with 401."""
    token = mint_dev_token(SECRET, sub="u-1", roles=["Coach"], ttl=timedelta(seconds=-1))
    with pytest.raises(HTTPException) as exc:
        DevTokenVerifier(SECRET).verify(token)
    assert exc.value.status_code == 401


def test_wrong_secret_is_rejected() -> None:
    """A dev token signed with a different secret is rejected with 401."""
    token = mint_dev_token(SECRET, sub="u-1", roles=["Coach"])
    with pytest.raises(HTTPException) as exc:
        DevTokenVerifier("other-secret").verify(token)
    assert exc.value.status_code == 401


def test_booking_style_single_role_claim_is_accepted() -> None:
    # strata.booking's Phase-1 tokens carry role="coach" (lowercase, singular).
    # exp is required on every dev token (see the verifiers module docstring).
    """Booking's Phase-1 singular lowercase role claim is accepted and normalised."""
    token = pyjwt.encode(
        {"sub": "c-1", "role": "coach", "exp": _in_an_hour()}, SECRET, algorithm="HS256"
    )
    principal = DevTokenVerifier(SECRET).verify(token)
    assert principal.roles == ["Coach"]


def test_token_without_exp_is_rejected() -> None:
    # PyJWT only validates exp when present — the verifier requires the claim,
    # so a signed-but-expiry-less token can't live forever.
    """A dev token without an exp claim is rejected: no token lives forever."""
    token = pyjwt.encode({"sub": "u-1", "roles": ["Coach"]}, SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        DevTokenVerifier(SECRET).verify(token)
    assert exc.value.status_code == 401


def test_unknown_role_values_are_ignored() -> None:
    """Role values outside the catalogue are dropped during verification."""
    token = mint_dev_token(SECRET, sub="u-1", roles=["coach", "superuser"])
    assert DevTokenVerifier(SECRET).verify(token).roles == ["Coach"]


def test_missing_secret_is_a_startup_error() -> None:
    """Constructing the dev verifier without a secret is a startup error."""
    with pytest.raises(RuntimeError, match="STRATA_DEV_AUTH_SECRET"):
        DevTokenVerifier("")


def test_factory_selects_dev_only_when_explicitly_local() -> None:
    """The verifier factory hands out dev mode only in an explicitly local environment."""
    verifier = create_verifier("dev", environment="local", dev_secret=SECRET)
    assert isinstance(verifier, DevTokenVerifier)
    with pytest.raises(RuntimeError, match="EXPLICITLY set to 'local'"):
        create_verifier("dev", environment="production", dev_secret=SECRET)


def test_factory_fails_closed_when_the_environment_is_unset() -> None:
    # None means "STRATA_ENVIRONMENT was never set" — a consumer whose Settings
    # merely *defaults* to local must not get a dev verifier by omission.
    """With the environment unset the factory fails closed rather than defaulting to dev."""
    with pytest.raises(RuntimeError, match="EXPLICITLY set to 'local'"):
        create_verifier("dev", environment=None, dev_secret=SECRET)


def test_factory_requires_cognito_settings() -> None:
    """Cognito mode requires the full pool settings; partial config refuses to start."""
    with pytest.raises(RuntimeError, match="cognito auth mode needs"):
        create_verifier("cognito", environment="local", region="eu-west-2")
    verifier = create_verifier(
        "cognito",
        environment="production",
        region="eu-west-2",
        user_pool_id="eu-west-2_TEST",
        client_id="client-1",
    )
    assert isinstance(verifier, CognitoTokenVerifier)


def test_signed_token_without_a_subject_is_401_not_500() -> None:
    """A validly signed dev token missing its subject is a 401, never a 500."""
    token = pyjwt.encode({"roles": ["coach"], "exp": _in_an_hour()}, SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        DevTokenVerifier(SECRET).verify(token)
    assert exc.value.status_code == 401
    assert "no subject" in exc.value.detail


def test_mint_with_email_populates_the_principal() -> None:
    # The email claim enables offline dev-mode provisioning (GetUser can never
    # succeed against a locally minted token).
    """A dev token minted with an email carries it onto the principal (offline provisioning)."""
    token = mint_dev_token(SECRET, sub="u-1", roles=["Coach"], email="dev@wandahealth.com")
    assert DevTokenVerifier(SECRET).verify(token).email == "dev@wandahealth.com"


def test_mint_without_email_leaves_the_principal_email_unset() -> None:
    """A dev token minted without an email leaves the principal's email unset."""
    token = mint_dev_token(SECRET, sub="u-1", roles=["Coach"])
    assert DevTokenVerifier(SECRET).verify(token).email is None
