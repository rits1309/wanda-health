"""API tests for the coach calendar: layers, appointment attachment, authz, validation."""

from fastapi.testclient import TestClient

from tests.auth import bearer

COACH_C1 = bearer("c-1", "coach")
COACH_C2 = bearer("c-2", "coach")
MEMBER = bearer("m-1", "member")
ADMIN = bearer("a-1", "admin")

# The seed books three of c-1's six explicit slots "tomorrow" (relative to the seed run).
WINDOW = {"from": "2020-01-01", "to": "2099-12-31"}


def test_calendar_returns_layers_with_appointments(client: TestClient) -> None:
    """A coach's calendar returns layered entries, with appointments on booked slots."""
    resp = client.get("/v1/coaches/c-1/calendar", params=WINDOW, headers=COACH_C1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["coach_id"] == "c-1"
    statuses = {e["status"] for e in body["entries"]}
    assert "booked" in statuses and "available" in statuses
    booked = [e for e in body["entries"] if e["status"] == "booked"]
    assert all(e["appointment"] is not None for e in booked)
    assert {e["appointment"]["member_id"] for e in booked} >= {"m-1"}
    available = [e for e in body["entries"] if e["status"] == "available"]
    assert all(e["appointment"] is None for e in available)


def test_calendar_carries_kernel_display_names(client: TestClient) -> None:
    """Calendar payloads name the coach and booked members server-side."""
    resp = client.get("/v1/coaches/c-1/calendar", params=WINDOW, headers=COACH_C1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["coach_name"] == "Casey Ellis"
    booked = [e["appointment"] for e in body["entries"] if e["status"] == "booked"]
    assert booked  # the assertion below must inspect real appointments
    names = {a["member_id"]: a["member_name"] for a in booked}
    assert names["m-1"] == "Morgan Lee"
    assert all(name for name in names.values())


def test_calendar_authorisation(client: TestClient) -> None:
    """Only the coach themself or an admin can read a coach calendar."""
    assert (
        client.get("/v1/coaches/c-1/calendar", params=WINDOW, headers=COACH_C2).status_code == 403
    )
    assert client.get("/v1/coaches/c-1/calendar", params=WINDOW, headers=MEMBER).status_code == 403
    assert client.get("/v1/coaches/c-1/calendar", params=WINDOW, headers=ADMIN).status_code == 200


def test_calendar_rejects_inverted_window(client: TestClient) -> None:
    """An inverted from/to window is rejected with 400."""
    resp = client.get(
        "/v1/coaches/c-1/calendar",
        params={"from": "2026-12-31", "to": "2026-01-01"},
        headers=COACH_C1,
    )
    assert resp.status_code == 400
