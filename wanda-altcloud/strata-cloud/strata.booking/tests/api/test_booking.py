"""API tests for slot browse + appointments: roles, idempotency replay, header validation."""

from fastapi.testclient import TestClient

from tests.auth import bearer

MEMBER_M1 = bearer("m-1", "member")
MEMBER_M3 = bearer("m-3", "member")
COACH_C1 = bearer("c-1", "coach")
COACH_C2 = bearer("c-2", "coach")
ADMIN = bearer("a-1", "admin")

WINDOW = {"from": "2020-01-01", "to": "2099-12-31"}


def _an_available_seed_slot(client: TestClient, member: dict[str, str]) -> str:
    slots = client.get("/v1/slots", params={**WINDOW, "coach_id": "c-1"}, headers=member).json()
    assert slots, "expected available seed slots for c-1"
    return str(slots[0]["slot_id"])


def test_browse_is_member_only_and_hides_booked(client: TestClient) -> None:
    """Slot browsing is member-only and never shows already-booked slots."""
    assert client.get("/v1/slots", params=WINDOW, headers=COACH_C1).status_code == 403
    slots = client.get("/v1/slots", params=WINDOW, headers=MEMBER_M1)
    assert slots.status_code == 200
    booked_seed = {"seed-slot-c1-0", "seed-slot-c1-1", "seed-slot-c1-2"}
    assert not booked_seed & {s["slot_id"] for s in slots.json()}


def test_member_booking_is_idempotent_with_replay_200(client: TestClient) -> None:
    """Rebooking with the same idempotency key replays as 200, never a duplicate."""
    slot_id = _an_available_seed_slot(client, MEMBER_M3)
    body = {"slot_id": slot_id, "language_matched": False}
    headers = {**MEMBER_M3, "Idempotency-Key": f"m-3:{slot_id}"}

    first = client.post("/v1/appointments", json=body, headers=headers)
    assert first.status_code == 201, first.text
    replay = client.post("/v1/appointments", json=body, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]

    other = client.post("/v1/appointments", json={"slot_id": slot_id}, headers=MEMBER_M1)
    assert other.status_code == 409  # one member per slot


def test_mismatched_idempotency_key_is_422(client: TestClient) -> None:
    """An idempotency key that does not match the booking body is rejected with 422."""
    resp = client.post(
        "/v1/appointments",
        json={"slot_id": "seed-slot-c1-4"},
        headers={**MEMBER_M1, "Idempotency-Key": "wrong:key"},
    )
    assert resp.status_code == 422


def test_coach_books_on_behalf_only_on_own_slots(client: TestClient) -> None:
    """A coach can book on behalf of a member only onto their own slots."""
    slot_id = _an_available_seed_slot(client, MEMBER_M1)
    not_mine = client.post(
        "/v1/appointments",
        json={"slot_id": slot_id, "member_id": "m-1"},
        headers=COACH_C2,  # c-1's slot
    )
    assert not_mine.status_code == 403

    mine = client.post(
        "/v1/appointments",
        json={"slot_id": slot_id, "member_id": "m-1"},
        headers=COACH_C1,
    )
    assert mine.status_code == 201, mine.text
    assert mine.json()["created_by"] == "coach"


def test_appointment_reads_are_scoped(client: TestClient) -> None:
    """Appointment reads are scoped: own records only, admin sees programme-wide."""
    mine = client.get("/v1/appointments", headers=MEMBER_M1).json()
    assert mine and all(a["member_id"] == "m-1" for a in mine)

    appointment_id = mine[0]["id"]
    assert client.get(f"/v1/appointments/{appointment_id}", headers=MEMBER_M3).status_code == 403
    assert client.get(f"/v1/appointments/{appointment_id}", headers=ADMIN).status_code == 200

    coach_view = client.get("/v1/appointments", headers=COACH_C1).json()
    assert all(a["coach_id"] == "c-1" for a in coach_view)


def test_appointment_payloads_name_the_member(client: TestClient) -> None:
    """Appointment responses carry the member's kernel display name."""
    mine = client.get("/v1/appointments", headers=MEMBER_M1).json()
    assert mine and all(a["member_name"] == "Morgan Lee" for a in mine)
    single = client.get(f"/v1/appointments/{mine[0]['id']}", headers=MEMBER_M1).json()
    assert single["member_name"] == "Morgan Lee"


def test_dual_role_caller_books_themselves_the_member_way(client: TestClient) -> None:
    """A Member-who-also-holds-Coach self-books as a member (capability dispatch):
    no member_id required, created_by recorded as member."""
    dual = bearer("m-1", "member", "coach")
    slot_id = _an_available_seed_slot(client, MEMBER_M1)
    resp = client.post("/v1/appointments", json={"slot_id": slot_id}, headers=dual)
    assert resp.status_code == 201, resp.text
    assert resp.json()["member_id"] == "m-1"
    assert resp.json()["created_by"] == "member"


def test_no_login_member_is_bookable_end_to_end(client: TestClient) -> None:
    """The means test: Sam Nguyen (m-3) has NO email and NO
    Cognito Sub mapping — his coach books him, the appointment reads back and
    shows on the calendar exactly like any login-capable member's."""
    slot_id = _an_available_seed_slot(client, MEMBER_M1)
    booked = client.post(
        "/v1/appointments",
        json={"slot_id": slot_id, "member_id": "m-3"},
        headers=COACH_C1,
    )
    assert booked.status_code == 201, booked.text
    appointment_id = booked.json()["id"]

    detail = client.get(f"/v1/appointments/{appointment_id}", headers=COACH_C1)
    assert detail.status_code == 200
    assert detail.json()["member_id"] == "m-3"

    calendar = client.get("/v1/coaches/c-1/calendar", params=WINDOW, headers=COACH_C1).json()
    booked_members = {
        entry["appointment"]["member_id"]
        for entry in calendar["entries"]
        if entry.get("appointment")
    }
    assert "m-3" in booked_members  # indistinguishable from any other member
