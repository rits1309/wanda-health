"""API tests for the admin combined calendar + admin programme scoping."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.auth import bearer

ADMIN = bearer("a-1", "admin")
COACH = bearer("c-1", "coach")
OUTSIDER = bearer("a-x", "admin")

# The seed populates slots relative to the real "today", so the window tracks it too.
WINDOW = {
    "from": date.today().isoformat(),
    "to": (date.today() + timedelta(weeks=4)).isoformat(),
}


def test_admin_calendar_is_admin_only(client: TestClient) -> None:
    """The combined admin calendar rejects coach callers with 403."""
    resp = client.get("/v1/admin/calendar", params=WINDOW, headers=COACH)
    assert resp.status_code == 403


def test_admin_calendar_defaults_and_layers(client: TestClient) -> None:
    """The admin calendar defaults to all programme coaches with all three layers."""
    resp = client.get("/v1/admin/calendar", params=WINDOW, headers=ADMIN)
    assert resp.status_code == 200
    body = resp.json()
    assert {"c-1", "c-2", "c-3"} <= {c["coach_id"] for c in body["coaches"]}  # seed coaches
    assert body["layers"] == ["available", "booked", "unavailable"]

    booked_only = client.get(
        "/v1/admin/calendar",
        params={**WINDOW, "layers": ["booked"]},
        headers=ADMIN,
    ).json()
    statuses = {e["status"] for c in booked_only["coaches"] for e in c["entries"]}
    assert statuses <= {"booked"}


def test_admin_calendar_names_its_coaches(client: TestClient) -> None:
    """Every combined-calendar coach carries their effective name."""
    resp = client.get("/v1/admin/calendar", params=WINDOW, headers=ADMIN)
    assert resp.status_code == 200
    names = {c["coach_id"]: c["coach_name"] for c in resp.json()["coaches"]}
    assert names["c-1"] == "Casey Ellis"
    assert names["c-2"] == "Dana"  # Dana Reyes' cast override
    assert names["c-3"] == "Elena Marti"


def test_admin_calendar_coach_filter_and_scoping(client: TestClient) -> None:
    """The coach filter narrows the admin calendar; coaches outside the scope are refused."""
    filtered = client.get(
        "/v1/admin/calendar",
        params={**WINDOW, "coach_ids": ["c-1"]},
        headers=ADMIN,
    ).json()
    assert [c["coach_id"] for c in filtered["coaches"]] == ["c-1"]

    outside = client.get(
        "/v1/admin/calendar",
        params={**WINDOW, "coach_ids": ["c-1"]},
        headers=OUTSIDER,
    )
    assert outside.status_code == 403


def test_admin_appointment_access_is_programme_scoped(client: TestClient) -> None:
    """Admin appointment reads are programme-scoped: an outside admin gets 403."""
    assert client.get("/v1/appointments/seed-appt-0", headers=ADMIN).status_code == 200
    assert client.get("/v1/appointments/seed-appt-0", headers=OUTSIDER).status_code == 403
