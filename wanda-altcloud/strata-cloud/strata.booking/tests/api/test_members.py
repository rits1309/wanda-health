"""API tests for the scoped members listing: scoping, authz, payload."""

from fastapi.testclient import TestClient

from tests.auth import bearer

COACH_C1 = bearer("c-1", "coach")
COACH_C2 = bearer("c-2", "coach")
DANA_ADMIN_ONLY = bearer("c-2", "admin")
DANA_BOTH = bearer("c-2", "coach", "admin")
MEMBER = bearer("m-1", "member")
ADMIN = bearer("a-1", "admin")

# The seeded cast by display name: m-1..m-4 are p-1 members, m-5 is p-2's.
# Effective display names: Sam Carter renders his "Sam" override.
# Marta Iglesias (m-6) is the seeded migrated stand-in — converged into p-1 from a
# clean seed via her Legacy Django ID mapping, the fixture's Theo replacement.
P1_MEMBERS = ["Luis Ortega", "Marta Iglesias", "Morgan Lee", "Sam", "Sam Nguyen"]
ALL_MEMBERS = ["Luis Ortega", "Marta Iglesias", "Morgan Lee", "Priya Patel", "Sam", "Sam Nguyen"]


def _names(body: list[dict[str, str]]) -> list[str]:
    return [member["display_name"] for member in body]


def test_coach_lists_own_programme_members(client: TestClient) -> None:
    """A coach receives exactly their programmes' members, ordered by display name."""
    resp = client.get("/v1/members", headers=COACH_C1)
    assert resp.status_code == 200
    assert _names(resp.json()) == P1_MEMBERS  # Priya (p-2 only) is absent


def test_two_programme_coach_gets_both_programmes(client: TestClient) -> None:
    """A coach assigned to two programmes receives both programmes' members."""
    resp = client.get("/v1/members", headers=COACH_C2)
    assert resp.status_code == 200
    assert _names(resp.json()) == ALL_MEMBERS


def test_admin_lists_administered_programme_members(client: TestClient) -> None:
    """An admin without coach_id gets the members of every administered programme."""
    resp = client.get("/v1/members", headers=ADMIN)
    assert resp.status_code == 200
    assert _names(resp.json()) == ALL_MEMBERS


def test_scoped_admin_is_limited_to_administered_programmes(client: TestClient) -> None:
    """A scoped admin sees only administered members — Dana reaches p-1, never p-2."""
    resp = client.get("/v1/members", headers=DANA_ADMIN_ONLY)
    assert resp.status_code == 200
    assert _names(resp.json()) == P1_MEMBERS


def test_multi_role_caller_gets_the_capability_union(client: TestClient) -> None:
    """A Coach-and-Admin caller receives the union of both capabilities' programmes."""
    resp = client.get("/v1/members", headers=DANA_BOTH)
    assert resp.status_code == 200
    assert _names(resp.json()) == ALL_MEMBERS


def test_union_admin_half_stands_alone(client: TestClient) -> None:
    """The union's admin half stands alone — a-1 has no coach rows, only admin can fill it."""
    resp = client.get("/v1/members", headers=bearer("a-1", "coach", "admin"))
    assert resp.status_code == 200
    assert _names(resp.json()) == ALL_MEMBERS


def test_admin_coach_id_narrows_to_the_acted_for_coach(client: TestClient) -> None:
    """An admin acting for a coach receives that coach's programmes' members only."""
    resp = client.get("/v1/members", params={"coach_id": "c-1"}, headers=ADMIN)
    assert resp.status_code == 200
    assert _names(resp.json()) == P1_MEMBERS


def test_scoped_admin_acting_for_a_shared_coach_stays_in_remit(client: TestClient) -> None:
    """A scoped admin acting for a shared coach sees only administered members."""
    resp = client.get("/v1/members", params={"coach_id": "c-2"}, headers=DANA_ADMIN_ONLY)
    assert resp.status_code == 200
    assert _names(resp.json()) == P1_MEMBERS  # never p-2's Priya: Dana administers p-1 only


def test_admin_cannot_act_for_a_coach_outside_their_remit(client: TestClient) -> None:
    """An admin naming a coach sharing no administered programme is refused with 403."""
    resp = client.get("/v1/members", params={"coach_id": "m-1"}, headers=DANA_ADMIN_ONLY)
    assert resp.status_code == 403


def test_coach_cannot_name_another_coach(client: TestClient) -> None:
    """A coach passing another coach's id is refused with 403 (coaches act only for themselves)."""
    resp = client.get("/v1/members", params={"coach_id": "c-2"}, headers=COACH_C1)
    assert resp.status_code == 403


def test_coach_with_no_programmes_gets_an_empty_list(client: TestClient) -> None:
    """A coach-role holder with no programme assignments gets an empty list, not an error."""
    resp = client.get("/v1/members", headers=bearer("a-1", "coach"))
    assert resp.status_code == 200
    assert resp.json() == []


def test_member_role_is_refused(client: TestClient) -> None:
    """An authenticated caller holding neither Coach nor Admin is refused with 403."""
    assert client.get("/v1/members", headers=MEMBER).status_code == 403


def test_unauthenticated_is_refused(client: TestClient) -> None:
    """A caller without a valid token is refused with 401."""
    assert client.get("/v1/members").status_code == 401


def test_payload_carries_no_email_and_no_extra_fields(client: TestClient) -> None:
    """Every element carries exactly 's field set — no email, nothing more."""
    resp = client.get("/v1/members", headers=COACH_C1)
    assert resp.status_code == 200
    body = resp.json()
    assert body  # the guard must inspect real elements, not pass vacuously
    for item in body:
        assert set(item) == {"id", "display_name", "preferred_language", "timezone"}


def test_fields_come_from_the_kernel_profile(client: TestClient) -> None:
    """Values come from the kernel profile — Sam Carter renders his "Sam" override."""
    resp = client.get("/v1/members", headers=COACH_C1)
    by_id = {member["id"]: member for member in resp.json()}
    assert by_id["m-2"] == {
        "id": "m-2",
        "display_name": "Sam",  # the cast's member-side override
        "preferred_language": "es",
        "timezone": "Europe/London",
    }
