"""API tests for reschedule endpoints: role guards, scoping, and wiring."""

from fastapi.testclient import TestClient

from tests.auth import bearer

M1 = bearer("m-1", "member")
M3 = bearer("m-3", "member")
C1 = bearer("c-1", "coach")


def test_reschedule_is_member_only(client: TestClient) -> None:
    """Direct rescheduling is member-only; coaches get 403."""
    resp = client.post("/v1/appointments/seed-appt-0/reschedule", json={"slot_id": "x"}, headers=C1)
    assert resp.status_code == 403


def test_reschedule_request_is_coach_or_admin_only(client: TestClient) -> None:
    """Requesting a reschedule link is coach/admin-only; members get 403."""
    resp = client.post("/v1/appointments/seed-appt-0/reschedule-request", headers=M1)
    assert resp.status_code == 403


def test_history_is_scoped(client: TestClient) -> None:
    """Appointment history is visible to its member only; others get 403."""
    own = client.get("/v1/appointments/seed-appt-0/history", headers=M1)
    assert own.status_code == 200
    body = own.json()
    assert [a["id"] for a in body["appointments"]] == ["seed-appt-0"]
    assert body["reschedules"] == []

    other = client.get("/v1/appointments/seed-appt-0/history", headers=M3)
    assert other.status_code == 403


def test_unknown_appointment_is_404(client: TestClient) -> None:
    """History for an unknown appointment is 404."""
    assert client.get("/v1/appointments/nope/history", headers=M1).status_code == 404
