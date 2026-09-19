"""The wired verification seam rejects bad tokens — through the real dependency.

The seam itself is unit-tested in strata.identity; these tests prove THIS
service consumes it correctly (401s happen before any database use), including
the fail-closed dev-mode wiring (an unset STRATA_ENVIRONMENT never enables dev
mode) and the JWKS-outage 503. The valid-token path is exercised end-to-end in
tests/test_profiles_api.py.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from conftest import make_token
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi.testclient import TestClient
from strata_identity.security import DevTokenVerifier

from strata_engine_auth.core.auth import build_verifier, explicit_environment, seam
from strata_engine_auth.core.config import Settings


def test_missing_token_is_401(client: TestClient) -> None:
    """A request without a bearer token is rejected with 401."""
    assert client.get("/v1/auth/me").status_code == 401


def test_garbage_token_is_401(client: TestClient) -> None:
    """A malformed bearer token is rejected with 401."""
    assert client.get("/v1/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_id_tokens_are_rejected(client: TestClient, private_key: RSAPrivateKey) -> None:
    """An id token is not an access token: token_use=id is rejected."""
    token = make_token(private_key, token_use="id")
    response = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Token not valid for this client"


def test_other_clients_tokens_are_rejected(client: TestClient, private_key: RSAPrivateKey) -> None:
    """A token minted for a different client id is rejected."""
    token = make_token(private_key, client_id="other-client")
    assert (
        client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )


def test_expired_tokens_are_rejected(client: TestClient, private_key: RSAPrivateKey) -> None:
    """An expired token is rejected."""
    expired = int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())
    token = make_token(private_key, exp=expired)
    assert (
        client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )


def test_wrong_issuer_is_rejected(client: TestClient, private_key: RSAPrivateKey) -> None:
    """A token from a different issuer (another pool) is rejected."""
    other = "https://cognito-idp.eu-west-2.amazonaws.com/eu-west-2_OTHER"
    token = make_token(private_key, iss=other)
    assert (
        client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )


def test_a_jwks_outage_is_503_not_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, private_key: RSAPrivateKey
) -> None:
    """Infrastructure failure must not read as session expiry (documented on /me)."""

    def _boom(token: str) -> None:
        raise pyjwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    monkeypatch.setattr(
        seam._verifier, "_jwks_client", SimpleNamespace(get_signing_key_from_jwt=_boom)
    )
    token = make_token(private_key)
    response = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503
    assert response.json()["detail"] == "Token verification temporarily unavailable"


# --- the fail-closed dev-mode wiring (create_verifier only sees an EXPLICIT env) ---


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.delenv("STRATA_ENVIRONMENT", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # never read a developer's .env in tests


def test_environment_default_is_not_treated_as_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Settings environment default does not count as an explicit choice."""
    settings = _settings(monkeypatch)
    assert settings.environment == "local"  # the Settings default...
    assert explicit_environment(settings) is None  # ...is not an explicit choice


def test_environment_from_the_env_var_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """STRATA_ENVIRONMENT set in the environment counts as an explicit choice."""
    assert explicit_environment(_settings(monkeypatch, STRATA_ENVIRONMENT="local")) == "local"


def test_dev_mode_with_an_unset_environment_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev auth mode refuses to start unless the environment is explicitly local."""
    settings = _settings(monkeypatch, STRATA_AUTH_MODE="dev", STRATA_DEV_AUTH_SECRET="s3cret")
    with pytest.raises(RuntimeError, match="EXPLICITLY set to 'local'"):
        build_verifier(settings)


def test_dev_mode_with_an_explicitly_local_environment_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev auth mode starts once the environment is explicitly local."""
    settings = _settings(
        monkeypatch,
        STRATA_AUTH_MODE="dev",
        STRATA_DEV_AUTH_SECRET="s3cret",
        STRATA_ENVIRONMENT="local",
    )
    assert isinstance(build_verifier(settings), DevTokenVerifier)
