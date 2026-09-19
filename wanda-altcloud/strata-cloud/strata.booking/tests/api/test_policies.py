"""API tests for cancellation policies + the cancel endpoint."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from strata_booking.core import clock as clock_module
from tests.auth import bearer

COACH_C2 = bearer("c-2", "coach")
MEMBER_M1 = bearer("m-1", "member")
ADMIN = bearer("a-1", "admin")


def test_effective_policy_reflects_seeded_coach_override(client: TestClient) -> None:
    """Seed: p-1 default 24h/both; c-2 override 48h/coach-only."""
    c2 = client.get(
        "/v1/cancellation-policies/effective",
        params={"programme_id": "p-1", "coach_id": "c-2"},
        headers=COACH_C2,
    )
    assert c2.status_code == 200
    assert c2.json() == {
        "source": "coach",
        "cancellation_window_hours": 48,
        "cancellation_allowed_by": "coach",
    }
    c1 = client.get(
        "/v1/cancellation-policies/effective",
        params={"programme_id": "p-1", "coach_id": "c-1"},
        headers=ADMIN,
    )
    assert c1.json()["source"] == "programme"


def test_policy_writes_are_admin_only_and_programme_scoped(client: TestClient) -> None:
    """Cancellation-policy writes are admin-only and scoped to the admin's programmes."""
    body = {
        "scope": "programme",
        "programme_id": "p-1",
        "cancellation_window_hours": 24,
        "cancellation_allowed_by": "both",
    }
    assert client.post("/v1/cancellation-policies", json=body, headers=COACH_C2).status_code == 403
    outside = bearer("a-x", "admin")
    assert client.post("/v1/cancellation-policies", json=body, headers=outside).status_code == 403
    created = client.post("/v1/cancellation-policies", json=body, headers=ADMIN)
    assert created.status_code == 201
    assert created.json()["version"] >= 2  # supersedes the seeded programme policy

    listed = client.get(
        "/v1/cancellation-policies", params={"programme_id": "p-1"}, headers=ADMIN
    ).json()
    programme_versions = [p for p in listed if p["scope"] == "programme"]
    assert sum(1 for p in programme_versions if p["active"]) == 1


def test_cancel_endpoint_reports_clear_reasons(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seeded appointment seed-appt-2 belongs to m-4 (c-1). Pin "now" to 12h before its start so
    # it is always inside the 24h window and cancellation is rejected with the window reason
    # (the seed's wall-clock-relative dates must not decide this test's outcome).
    """Cancel inside the policy window is refused with the window stated as the reason."""
    m4 = bearer("m-4", "member")
    # Minted pre-pin: bearer tokens are validated against wall time.
    m4_headers, m1_headers = m4, MEMBER_M1
    appointment = client.get("/v1/appointments/seed-appt-2", headers=m4_headers).json()
    pinned = datetime.fromisoformat(appointment["start_utc"]) - timedelta(hours=12)
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: pinned)
    resp = client.post(
        "/v1/appointments/seed-appt-2/cancel", json={"reason": "test"}, headers=m4_headers
    )
    assert resp.status_code == 409
    assert "window" in resp.json()["detail"].lower()

    # Another member cannot cancel it at all.
    resp = client.post("/v1/appointments/seed-appt-2/cancel", json={}, headers=m1_headers)
    assert resp.status_code == 403
