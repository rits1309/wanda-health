"""API tests for reassignment endpoints: role guard + scoping."""

from fastapi.testclient import TestClient

from tests.auth import bearer

MEMBER = bearer("m-1", "member")
COACH = bearer("c-1", "coach")


def test_reassignment_is_coach_or_admin_only(client: TestClient) -> None:
    """Reassignment options and actions are coach/admin-only; members get 403."""
    assert (
        client.get("/v1/appointments/seed-appt-0/reassignment-options", headers=MEMBER).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/appointments/seed-appt-0/reassign", json={"slot_id": "x"}, headers=MEMBER
        ).status_code
        == 403
    )


def test_unknown_appointment_is_404(client: TestClient) -> None:
    """Reassignment options for an unknown appointment are 404."""
    resp = client.get("/v1/appointments/nope/reassignment-options", headers=COACH)
    assert resp.status_code == 404
