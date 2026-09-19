"""401 guards — every reference-data endpoint sits behind the verification seam.

The service accepts **any authenticated user**: a valid token of any
role passes (the positive path is every other api test, whose ``client`` carries one); no
token, a malformed token, and an expired token are all rejected with 401. ``/health`` stays
open (asserted in ``tests/test_baseline.py``).
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from strata_identity.roles import ROLE_COACH
from strata_identity.security import mint_dev_token

from strata_terminology.core.config import settings

ENDPOINTS = [
    "/v1/medications/search",
    "/v1/procedures/search",
    "/v1/diagnoses/search",
]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_no_token_is_rejected(anon_client: TestClient, path: str) -> None:
    """Every /v1 search route rejects a request without a token (401)."""
    assert anon_client.get(path, params={"q": "a"}).status_code == 401


@pytest.mark.parametrize("path", ENDPOINTS)
def test_malformed_token_is_rejected(anon_client: TestClient, path: str) -> None:
    """Every /v1 search route rejects a malformed bearer token (401)."""
    resp = anon_client.get(path, params={"q": "a"}, headers={"Authorization": "Bearer not-a-token"})
    assert resp.status_code == 401


@pytest.mark.parametrize("path", ENDPOINTS)
def test_expired_token_is_rejected(anon_client: TestClient, path: str) -> None:
    """Every /v1 search route rejects an expired token (401)."""
    assert settings.dev_auth_secret is not None
    expired = mint_dev_token(
        settings.dev_auth_secret, "t-1", [ROLE_COACH], ttl=timedelta(seconds=-60)
    )
    resp = anon_client.get(path, params={"q": "a"}, headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
