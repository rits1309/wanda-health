"""The FastAPI dependencies: bearer parsing and role guards on a real (test) app."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from strata_identity.roles import ROLE_ADMIN
from strata_identity.security import AuthSeam, DevTokenVerifier, Principal, mint_dev_token

SECRET = "test-secret"  # noqa: S105 — test fixture value

seam = AuthSeam(DevTokenVerifier(SECRET))
app = FastAPI()


@app.get("/whoami")
def whoami(principal: Annotated[Principal, Depends(seam.current_principal)]) -> dict[str, object]:
    return {"sub": principal.sub, "roles": principal.roles}


@app.get("/admin-only")
def admin_only(
    principal: Annotated[Principal, Depends(seam.require_role(ROLE_ADMIN))],
) -> dict[str, str]:
    return {"sub": principal.sub}


client = TestClient(app)


def _auth(roles: list[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_dev_token(SECRET, sub='u-1', roles=roles)}"}


def test_missing_token_is_401() -> None:
    """A request without a bearer token is rejected with 401."""
    assert client.get("/whoami").status_code == 401


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_bearer_scheme_is_case_insensitive(scheme: str) -> None:
    # RFC 7235: auth schemes compare case-insensitively (fastapi's HTTPBearer
    # accepted 'bearer' too); the token itself is untouched.
    """The Bearer scheme compares case-insensitively (RFC 7235)."""
    token = mint_dev_token(SECRET, sub="u-1", roles=["Coach"])
    response = client.get("/whoami", headers={"Authorization": f"{scheme} {token}"})
    assert response.status_code == 200
    assert response.json()["sub"] == "u-1"


def test_scheme_without_a_space_is_401() -> None:
    """A malformed Authorization header (no space after the scheme) is rejected with 401."""
    token = mint_dev_token(SECRET, sub="u-1", roles=["Coach"])
    assert client.get("/whoami", headers={"Authorization": f"Bearer{token}"}).status_code == 401


def test_garbage_token_is_401() -> None:
    """A garbage bearer token is rejected with 401."""
    assert client.get("/whoami", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_authenticated_caller_reaches_the_route() -> None:
    """An authenticated caller reaches the guarded route and sees their principal."""
    response = client.get("/whoami", headers=_auth(["Coach"]))
    assert response.status_code == 200
    assert response.json() == {"sub": "u-1", "roles": ["Coach"]}


def test_wrong_role_is_403() -> None:
    """A caller lacking the required role is rejected with 403, not 401."""
    assert client.get("/admin-only", headers=_auth(["Coach"])).status_code == 403


def test_multi_role_caller_passes_the_guard() -> None:
    """A multi-role caller passes a guard requiring any one of their roles."""
    response = client.get("/admin-only", headers=_auth(["Coach", "Admin"]))
    assert response.status_code == 200


def test_principal_repr_hides_the_token() -> None:
    """The principal never leaks the raw token via repr or model_dump."""
    principal = DevTokenVerifier(SECRET).verify(mint_dev_token(SECRET, sub="u-1", roles=[]))
    assert principal.token not in repr(principal)
    assert principal.token not in principal.model_dump()
