"""API tests for /v1/availability-patterns: CRUD, role boundaries, admin act-for-coach."""

from fastapi.testclient import TestClient

from tests.auth import bearer

COACH_C2 = bearer("c-2", "coach")
MEMBER = bearer("m-1", "member")
ADMIN = bearer("a-1", "admin")

# Late-evening window: avoids colliding with seeded/fuzzed slots for the same coaches.
BODY = {
    "days_of_week": [5],  # Saturdays
    "start_time_local": "22:00:00",
    "end_time_local": "23:00:00",
    "slot_duration_minutes": 30,
    "timezone": "America/Chicago",
    "active_from": "2026-10-01",
    "active_to": "2026-10-31",
}


def test_coach_crud_lifecycle(client: TestClient) -> None:
    """A coach creates, lists, updates and deletes their own availability pattern."""
    created = client.post("/v1/availability-patterns", json=BODY, headers=COACH_C2)
    assert created.status_code == 201, created.text
    pattern = created.json()
    assert pattern["coach_id"] == "c-2"
    assert pattern["version"] == 1
    pid = pattern["id"]

    listed = client.get("/v1/availability-patterns", headers=COACH_C2)
    assert pid in [p["id"] for p in listed.json()]

    updated = client.put(
        f"/v1/availability-patterns/{pid}",
        json={**BODY, "slot_duration_minutes": 20},
        headers=COACH_C2,
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    impact = client.post(f"/v1/availability-patterns/{pid}/impact", json=None, headers=COACH_C2)
    assert impact.status_code == 200
    assert impact.json()["cancelled_appointments"] == []

    deleted = client.delete(f"/v1/availability-patterns/{pid}", headers=COACH_C2)
    assert deleted.status_code == 204
    assert client.get(f"/v1/availability-patterns/{pid}", headers=COACH_C2).status_code == 404


def test_requires_authentication(client: TestClient) -> None:
    """Availability routes reject unauthenticated requests with 401."""
    assert client.get("/v1/availability-patterns").status_code == 401


def test_member_role_is_rejected(client: TestClient) -> None:
    """Members cannot create availability patterns (403)."""
    assert client.post("/v1/availability-patterns", json=BODY, headers=MEMBER).status_code == 403


def test_coach_cannot_act_for_another_coach(client: TestClient) -> None:
    """A coach cannot create patterns on another coach's behalf (403)."""
    resp = client.post(
        "/v1/availability-patterns",
        json=BODY,
        params={"coach_id": "c-1"},
        headers=COACH_C2,
    )
    assert resp.status_code == 403


def test_admin_acts_for_programme_coach_but_needs_coach_id(client: TestClient) -> None:
    """An admin must name the target coach (422 without), then acts for programme coaches."""
    no_target = client.get("/v1/availability-patterns", headers=ADMIN)
    assert no_target.status_code == 422

    for_coach = client.get("/v1/availability-patterns", params={"coach_id": "c-3"}, headers=ADMIN)
    assert for_coach.status_code == 200


def test_admin_scope_is_programme_derived(client: TestClient) -> None:
    """Admin availability access is scoped to the admin's programmes, not global."""
    outside = bearer("a-x", "admin")
    resp = client.get("/v1/availability-patterns", params={"coach_id": "c-3"}, headers=outside)
    assert resp.status_code == 403


def test_invalid_pattern_bodies_are_422(client: TestClient) -> None:
    """Invalid pattern bodies (bad timezone, inverted times) are rejected with 422."""
    bad_tz = client.post(
        "/v1/availability-patterns",
        json={**BODY, "timezone": "Mars/Olympus"},
        headers=COACH_C2,
    )
    assert bad_tz.status_code == 422
    bad_times = client.post(
        "/v1/availability-patterns",
        json={**BODY, "start_time_local": "23:30:00"},
        headers=COACH_C2,
    )
    assert bad_times.status_code == 422


def test_dual_role_coach_admin_self_serves_as_coach(client: TestClient) -> None:
    """Dana's case (the cast's Coach+Admin, review): a dual-role principal
    self-serves as a coach — no coach_id demanded, no admin programme check."""
    dana = bearer("c-2", "coach", "admin")
    resp = client.get("/v1/availability-patterns", headers=dana)
    assert resp.status_code == 200, resp.text
    assert all(p["coach_id"] == "c-2" for p in resp.json())
