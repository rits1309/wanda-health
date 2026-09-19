"""API tests for the auth routes (login / refresh / me): happy paths, error
mapping, bearer handling. Signup/confirm are retired (Identity)
their absence is pinned in tests/test_profiles_api.py."""

from typing import Any

import pytest
from botocore.exceptions import ClientError
from conftest import LOGIN_RESULT
from fastapi.testclient import TestClient

from strata_engine_auth.api._errors import ERROR_STATUS
from strata_engine_auth.core.config import settings
from strata_engine_auth.services import cognito

# The real function, captured before the autouse fixture fakes the module attribute.
_real_refresh = cognito.refresh


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "CognitoOperation")


def test_login_returns_the_token_set(client: TestClient) -> None:
    """Login returns the full Bearer token set (access, id, refresh, expiry)."""
    response = client.post(
        "/v1/auth/login", json={"identifier": "user@wandahealth.com", "password": "Passw0rd!2026"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "access-tok"
    assert body["id_token"] == "id-tok"
    assert body["refresh_token"] == "refresh-tok"
    assert body["expires_in"] == 3600
    assert body["token_type"] == "Bearer"


def test_refresh_returns_fresh_tokens_without_a_refresh_token(client: TestClient) -> None:
    """Refresh returns fresh tokens; Cognito does not rotate the refresh token in this flow."""
    response = client.post("/v1/auth/refresh", json={"refresh_token": "refresh-tok"})
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "access-tok-2"
    assert body["refresh_token"] is None  # Cognito does not rotate it in this flow


def test_refresh_passes_the_sub_to_the_seam(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refresh route forwards the caller's sub to the Cognito seam."""
    seen: dict[str, Any] = {}

    def _refresh(refresh_token: str, sub: str | None = None) -> dict[str, Any]:
        seen.update(refresh_token=refresh_token, sub=sub)
        return {"AuthenticationResult": {"AccessToken": "a", "IdToken": "i", "ExpiresIn": 3600}}

    monkeypatch.setattr(cognito, "refresh", _refresh)
    response = client.post(
        "/v1/auth/refresh", json={"refresh_token": "refresh-tok", "sub": "uuid-username-1"}
    )
    assert response.status_code == 200
    assert seen == {"refresh_token": "refresh-tok", "sub": "uuid-username-1"}


def test_refresh_ignores_the_email_field_for_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SECRET_HASH must be keyed on the real Cognito username (the token's sub) —
    the pool's usernames are generated UUIDs, never the email."""
    captured: dict[str, Any] = {}

    class _FakeClient:
        def initiate_auth(self, **params: Any) -> dict[str, Any]:
            captured.update(params)
            return {"AuthenticationResult": {}}

    monkeypatch.setattr(cognito, "_cognito", lambda: _FakeClient())
    monkeypatch.setattr(settings, "cognito_client_secret", "shhh")
    _real_refresh("refresh-tok", "uuid-username-1")  # the fixture fakes cognito.refresh
    expected = cognito._secret_hash("uuid-username-1")
    assert captured["AuthParameters"]["SECRET_HASH"] == expected


def test_refresh_with_a_confidential_client_requires_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service backstop: a missing sub is a programming error (the route
    validates and answers 400 first), never a hash keyed on the wrong value."""
    monkeypatch.setattr(settings, "cognito_client_secret", "shhh")
    with pytest.raises(RuntimeError, match="requires sub"):
        _real_refresh("refresh-tok")  # the fixture fakes cognito.refresh


def test_refresh_route_maps_the_confidential_client_guard_to_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refresh without a sub on a confidential client maps the guard to a 400, not a 500."""
    monkeypatch.setattr(settings, "cognito_client_secret", "shhh")
    # Re-wire the route to the REAL refresh (the autouse fixture fakes it).
    monkeypatch.setattr(cognito, "refresh", _real_refresh)
    response = client.post(
        "/v1/auth/refresh", json={"refresh_token": "refresh-tok", "email": "u@wandahealth.com"}
    )
    assert response.status_code == 400
    assert "requires sub" in response.json()["detail"]


# /me (the enriched principal) is covered in tests/test_profiles_api.py (real DB)
# and tests/test_security.py (the wired seam's rejections).


@pytest.mark.parametrize(("code", "expected"), [*ERROR_STATUS.items(), ("SomethingNew", 400)])
def test_cognito_errors_map_to_http_statuses_on_refresh(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, code: str, expected: int
) -> None:
    """Refresh keeps the informative mapping (tokens are unguessable bearer
    material, not enumerable identifiers). Login's mapping died at — its
    generic collapse is pinned in tests/test_migration_login.py."""

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise _client_error(code)

    monkeypatch.setattr(cognito, "refresh", _raise)
    response = client.post("/v1/auth/refresh", json={"refresh_token": "rt"})
    assert response.status_code == expected
    assert response.json()["detail"] == "boom"


def test_login_accepts_a_username_identifier(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """one field takes a legacy app username or an email — the value
    passes through to the seam untouched (the pool's email alias disambiguates)."""
    seen: dict[str, str] = {}

    def _login(identifier: str, password: str) -> dict[str, Any]:
        seen["identifier"] = identifier
        return LOGIN_RESULT

    monkeypatch.setattr(cognito, "login", _login)
    response = client.post(
        "/v1/auth/login", json={"identifier": "c149pn2db0b", "password": "Pw!2026xx"}
    )
    assert response.status_code == 200
    assert seen["identifier"] == "c149pn2db0b"
