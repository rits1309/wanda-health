"""API tests for manual no-show marking: role guard + scoping."""

from fastapi.testclient import TestClient

from tests.auth import bearer


def test_no_show_is_coach_or_admin_only(client: TestClient) -> None:
    """Marking a no-show is coach/admin-only; members get 403."""
    member = bearer("m-1", "member")
    resp = client.post("/v1/appointments/seed-appt-0/no-show", headers=member)
    assert resp.status_code == 403


def test_no_show_unknown_appointment_is_404(client: TestClient) -> None:
    """Marking a no-show on an unknown appointment is 404."""
    coach = bearer("c-1", "coach")
    assert client.post("/v1/appointments/nope/no-show", headers=coach).status_code == 404
