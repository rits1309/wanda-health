"""API tests for unavailability endpoints: CRUD + role boundaries."""

from fastapi.testclient import TestClient

from tests.auth import bearer

COACH_C2 = bearer("c-2", "coach")
COACH_C3 = bearer("c-3", "coach")
MEMBER = bearer("m-1", "member")
ADMIN = bearer("a-1", "admin")

BLOCK = {
    "date": "2026-11-20",
    "start_time_local": "21:00:00",
    "end_time_local": "22:00:00",
    "timezone": "America/Chicago",
}
RECURRING = {
    "days_of_week": [6],  # Sundays — outside any seeded availability
    "start_time_local": "21:00:00",
    "end_time_local": "22:00:00",
    "timezone": "America/Chicago",
    "active_from": "2026-11-01",
    "active_to": "2026-11-30",
}


def test_block_lifecycle(client: TestClient) -> None:
    """A coach creates, lists and deletes a one-off unavailability block."""
    created = client.post("/v1/unavailability-blocks", json=BLOCK, headers=COACH_C2)
    assert created.status_code == 201, created.text
    block_id = created.json()["id"]

    listed = client.get("/v1/unavailability-blocks", headers=COACH_C2)
    assert block_id in [b["id"] for b in listed.json()]

    delete_response = client.delete(f"/v1/unavailability-blocks/{block_id}", headers=COACH_C2)
    assert delete_response.status_code == 204
    assert block_id not in [
        b["id"] for b in client.get("/v1/unavailability-blocks", headers=COACH_C2).json()
    ]


def test_recurring_lifecycle(client: TestClient) -> None:
    """A coach creates, lists and deletes a recurring unavailability pattern."""
    created = client.post("/v1/unavailability-patterns", json=RECURRING, headers=COACH_C2)
    assert created.status_code == 201, created.text
    pattern_id = created.json()["id"]

    listed = client.get("/v1/unavailability-patterns", headers=COACH_C2)
    assert pattern_id in [p["id"] for p in listed.json()]

    delete_response = client.delete(f"/v1/unavailability-patterns/{pattern_id}", headers=COACH_C2)
    assert delete_response.status_code == 204


def test_cross_coach_delete_is_403(client: TestClient) -> None:
    """A coach cannot delete another coach's unavailability block (403)."""
    created = client.post("/v1/unavailability-blocks", json=BLOCK, headers=COACH_C2)
    block_id = created.json()["id"]
    resp = client.delete(f"/v1/unavailability-blocks/{block_id}", headers=COACH_C3)
    assert resp.status_code == 403
    client.delete(f"/v1/unavailability-blocks/{block_id}", headers=COACH_C2)


def test_member_is_403_and_admin_acts_for_coach(client: TestClient) -> None:
    """Members cannot write unavailability; an admin acts for a named programme coach."""
    assert client.post("/v1/unavailability-blocks", json=BLOCK, headers=MEMBER).status_code == 403
    created = client.post(
        "/v1/unavailability-blocks",
        json=BLOCK,
        params={"coach_id": "c-3"},
        headers=ADMIN,
    )
    assert created.status_code == 201
    assert created.json()["coach_id"] == "c-3"
    client.delete(
        f"/v1/unavailability-blocks/{created.json()['id']}",
        params={"coach_id": "c-3"},
        headers=ADMIN,
    )
