"""Unit tests for booking's seam WIRING — the verifier itself is strata.identity's.

The shared verifiers (signature/expiry validation, the legacy lowercase ``role``
claim, JWKS handling) are tested in strata.identity's own suite; here we pin only
what this package owns: the dev-mode wiring verifies seam-minted tokens end to
end, the catalogue-role guards bind correctly and fail closed, and the principal
is re-keyed onto its kernel profile (``cognito_sub`` canonical, profile-id
fallback, 403 for a subject with no profile).
"""

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from strata_identity.security import mint_dev_token

from strata_booking.core.config import settings
from strata_booking.core.security import (
    ROLE_COACH,
    ROLE_MEMBER,
    CurrentPrincipal,
    Principal,
    require_role,
)


def _token(sub: str, roles: list[str]) -> str:
    assert settings.dev_auth_secret is not None
    return mint_dev_token(settings.dev_auth_secret, sub, roles)


def _probe_app() -> FastAPI:
    """A minimal app exercising the seam's dependencies end to end."""
    app = FastAPI()

    @app.get("/whoami")
    def whoami(principal: CurrentPrincipal) -> dict[str, object]:
        return {"sub": principal.sub, "roles": principal.roles}

    @app.get("/coach-only")
    def coach_only(
        principal: Annotated[Principal, Depends(require_role(ROLE_COACH))],
    ) -> dict[str, str]:
        return {"sub": principal.sub}

    return app


def test_missing_bearer_is_401() -> None:
    """A request without a bearer token is rejected with 401."""
    assert TestClient(_probe_app()).get("/whoami").status_code == 401


def test_profile_id_sub_authenticates_and_resolves_to_itself() -> None:
    """Dev tokens mint sub = the profile id — the fallback resolution path."""
    client = TestClient(_probe_app())
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {_token('c-1', [ROLE_COACH])}"})
    assert resp.status_code == 200
    assert resp.json() == {"sub": "c-1", "roles": ["Coach"]}


def test_cognito_sub_resolves_to_the_profile_id() -> None:
    """The canonical path: a token subject held as an active ``Cognito Sub``
    mapping in ``kernel.identifiers`` (real pool tokens, Identity).

    Exercised against the SEEDED cast mapping (``seed-sub-casey`` ↔ ``c-1``) rather
    than rows this test writes: the app resolves the subject on its own sessions,
    which cannot see the isolated test transaction's uncommitted data.
    """
    client = TestClient(_probe_app())
    resp = client.get(
        "/whoami", headers={"Authorization": f"Bearer {_token('seed-sub-casey', [ROLE_COACH])}"}
    )
    assert resp.status_code == 200
    assert resp.json()["sub"] == "c-1"  # re-keyed onto the profile id


def test_unknown_subject_fails_closed() -> None:
    """A token for a subject with no profile fails closed with 403."""
    client = TestClient(_probe_app())
    resp = client.get(
        "/whoami", headers={"Authorization": f"Bearer {_token('no-such-subject', [ROLE_COACH])}"}
    )
    assert resp.status_code == 403
    assert "profile" in resp.json()["detail"].lower()


def test_tampered_token_rejected() -> None:
    """A tampered token signature is rejected with 401."""
    client = TestClient(_probe_app())
    resp = client.get(
        "/whoami", headers={"Authorization": f"Bearer {_token('c-1', [ROLE_COACH])}x"}
    )
    assert resp.status_code == 401


def test_role_guard_enforced_via_http() -> None:
    """The role guard rejects a caller lacking the required role (403)."""
    client = TestClient(_probe_app())
    resp = client.get(
        "/coach-only", headers={"Authorization": f"Bearer {_token('m-1', [ROLE_MEMBER])}"}
    )
    assert resp.status_code == 403


def test_roleless_token_fails_the_guard_closed() -> None:
    """A token carrying no roles fails the role guard closed (403)."""
    client = TestClient(_probe_app())
    resp = client.get("/coach-only", headers={"Authorization": f"Bearer {_token('c-1', [])}"})
    assert resp.status_code == 403
