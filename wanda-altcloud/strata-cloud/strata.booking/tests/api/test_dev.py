"""API tests for the dev-only router: token minting, and absence when dev mode is off."""

import pytest
from fastapi.testclient import TestClient
from strata_identity.security import DevTokenVerifier

from strata_booking.core.config import settings
from strata_booking.main import create_app


def test_dev_tokens_endpoint_mints_a_valid_seam_token(client: TestClient) -> None:
    # The legacy body fields (coach_id/programme_ids) are accepted for back-compat
    # and ignored: the minted token carries sub + the catalogue role only.
    """The dev token endpoint mints a seam token carrying sub + catalogue role only."""
    resp = client.post(
        "/v1/dev/tokens",
        json={"sub": "c-1", "role": "coach", "coach_id": "c-1", "programme_ids": ["p-1"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert settings.dev_auth_secret is not None
    principal = DevTokenVerifier(settings.dev_auth_secret).verify(body["access_token"])
    assert principal.sub == "c-1"
    assert principal.roles == ["Coach"]


def test_dev_router_absent_when_dev_mode_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dev router does not exist when dev mode is off (404)."""
    monkeypatch.setattr(settings, "dev_mode", False)
    client = TestClient(create_app())
    resp = client.post("/v1/dev/tokens", json={"sub": "c-1", "role": "coach"})
    assert resp.status_code == 404


def test_call_record_roundtrip_and_notification_listing(client: TestClient) -> None:
    """Dev call records round-trip and dev notifications list recent sends."""
    created = client.post(
        "/v1/dev/call-records",
        json={"coach_id": "c-1", "member_id": "m-1", "initiated_at_utc": "2026-07-06T14:00:00Z"},
    )
    assert created.status_code == 200
    assert created.json()["initiated_at_utc"].startswith("2026-07-06T14:00:00")

    listed = client.get("/v1/dev/notifications", params={"limit": 5})
    assert listed.status_code == 200
    assert isinstance(listed.json(), list)
