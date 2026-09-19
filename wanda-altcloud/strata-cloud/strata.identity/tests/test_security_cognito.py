"""Cognito-mode verification against locally generated RSA keys — no network, no AWS.

Token forging and JWKS stubbing come from the shipped :mod:`strata_identity.testing`
helpers (the same ones consumer suites use), so the forgeries can't drift.
"""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import HTTPException

from strata_identity.security import CognitoTokenVerifier
from strata_identity.testing import generate_rsa_key, mint_cognito_token, stub_jwks_client

REGION, POOL, CLIENT = "eu-west-2", "eu-west-2_TEST", "client-1"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"


@pytest.fixture(scope="module")
def private_key() -> RSAPrivateKey:
    return generate_rsa_key()


@pytest.fixture
def verifier(private_key: RSAPrivateKey) -> CognitoTokenVerifier:
    v = CognitoTokenVerifier(REGION, POOL, CLIENT)
    # No network in tests: stub the JWKS client with the matching public key.
    stub_jwks_client(v, private_key)
    return v


def _token(private_key: RSAPrivateKey, **overrides: Any) -> str:
    return mint_cognito_token(
        private_key,
        sub="cognito-sub-1",
        client_id=CLIENT,
        issuer=ISSUER,
        groups=["coach", "admin"],
        extra_claims=overrides,
    )


def test_valid_access_token_maps_groups_to_roles(
    verifier: CognitoTokenVerifier, private_key: RSAPrivateKey
) -> None:
    """A valid access token verifies and its cognito:groups map to catalogue roles."""
    principal = verifier.verify(_token(private_key))
    assert principal.sub == "cognito-sub-1"
    assert principal.roles == ["Admin", "Coach"]


def test_unknown_groups_are_ignored(
    verifier: CognitoTokenVerifier, private_key: RSAPrivateKey
) -> None:
    """Non-role Cognito groups (e.g. SSO groups) are ignored during role mapping."""
    token = _token(private_key, **{"cognito:groups": ["coach", "eu-west-2_TEST_gsuite"]})
    assert verifier.verify(token).roles == ["Coach"]


def test_id_tokens_are_rejected(verifier: CognitoTokenVerifier, private_key: RSAPrivateKey) -> None:
    """An id token is rejected with 401: only access tokens are valid for this client."""
    with pytest.raises(HTTPException) as exc:
        verifier.verify(_token(private_key, token_use="id"))
    assert exc.value.status_code == 401
    assert "not valid for this client" in exc.value.detail


def test_other_clients_tokens_are_rejected(
    verifier: CognitoTokenVerifier, private_key: RSAPrivateKey
) -> None:
    """A token minted for a different client id is rejected with 401."""
    with pytest.raises(HTTPException) as exc:
        verifier.verify(_token(private_key, client_id="other-client"))
    assert exc.value.status_code == 401


def test_expired_tokens_are_rejected(
    verifier: CognitoTokenVerifier, private_key: RSAPrivateKey
) -> None:
    """An expired access token is rejected with 401."""
    expired = mint_cognito_token(
        private_key,
        sub="cognito-sub-1",
        client_id=CLIENT,
        issuer=ISSUER,
        expires_in=timedelta(minutes=-1),
    )
    with pytest.raises(HTTPException) as exc:
        verifier.verify(expired)
    assert exc.value.status_code == 401


def test_wrong_issuer_is_rejected(
    verifier: CognitoTokenVerifier, private_key: RSAPrivateKey
) -> None:
    """A token issued by a different user pool is rejected with 401."""
    other_pool = "https://cognito-idp.eu-west-2.amazonaws.com/eu-west-2_OTHER"
    with pytest.raises(HTTPException) as exc:
        verifier.verify(_token(private_key, iss=other_pool))
    assert exc.value.status_code == 401


def test_signed_token_without_a_subject_is_401_not_500(
    verifier: CognitoTokenVerifier, private_key: RSAPrivateKey
) -> None:
    """A validly signed token missing its subject is a 401, never a 500."""
    with pytest.raises(HTTPException) as exc:
        verifier.verify(_token(private_key, sub=None))
    assert exc.value.status_code == 401


def test_jwks_outage_is_503_not_401(private_key: RSAPrivateKey) -> None:
    """A JWKS fetch failure is infrastructure, not session expiry — 503, not 401."""
    verifier = CognitoTokenVerifier(REGION, POOL, CLIENT)

    def _boom(token: str) -> None:
        raise pyjwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    verifier._jwks_client = SimpleNamespace(  # type: ignore[assignment]
        get_signing_key_from_jwt=_boom
    )
    with pytest.raises(HTTPException) as exc:
        verifier.verify(_token(private_key))
    assert exc.value.status_code == 503
    assert exc.value.detail == "Token verification temporarily unavailable"


def test_unknown_signing_key_is_401_not_503(private_key: RSAPrivateKey) -> None:
    """A kid the pool doesn't know (foreign/forged token) is a verdict on the
    token — 401, never a 503 that hides an auth failure as an outage."""
    verifier = CognitoTokenVerifier(REGION, POOL, CLIENT)

    def _unknown_kid(token: str) -> None:
        raise pyjwt.PyJWKClientError('Unable to find a signing key that matches: "nope"')

    verifier._jwks_client = SimpleNamespace(  # type: ignore[assignment]
        get_signing_key_from_jwt=_unknown_kid
    )
    with pytest.raises(HTTPException) as exc:
        verifier.verify(_token(private_key))
    assert exc.value.status_code == 401


def test_access_tokens_carry_no_email_claim_so_email_is_none(
    verifier: CognitoTokenVerifier, private_key: RSAPrivateKey
) -> None:
    """Access tokens carry no email claim, so the principal's email is None."""
    assert verifier.verify(_token(private_key)).email is None


def test_email_claim_populates_the_principal(
    verifier: CognitoTokenVerifier, private_key: RSAPrivateKey
) -> None:
    """When an email claim is present it populates the principal."""
    principal = verifier.verify(_token(private_key, email="user@wandahealth.com"))
    assert principal.email == "user@wandahealth.com"
