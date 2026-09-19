"""Device registration at dispatch (b/c): create, supersede, reject."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import UserExternalId
from strata_core.fixtures import create_profile

from tests.conftest import EDGE_SECRET

AUTH = {"X-API-Key": EDGE_SECRET}
SCALE = "SmartMeter Scale"  # seeded catalogue name — a literal on purpose, it pins the seed


@pytest.fixture
async def members(session: AsyncSession) -> tuple[str, str]:
    """Two Members from the identity kernel to register devices against."""
    a = await create_profile(
        session,
        email="reg-a@wanda.test",
        first_name="Reg",
        last_name="Member A",
        cognito_sub="reg-test-sub-a",
    )
    b = await create_profile(
        session,
        email="reg-b@wanda.test",
        first_name="Reg",
        last_name="Member B",
        cognito_sub="reg-test-sub-b",
    )
    await session.commit()
    return a.id, b.id


def _register(client: TestClient, user_id: str, external_id: str) -> Any:
    return client.post(
        "/v1/device-registrations",
        json={"user_id": user_id, "identifier_type": SCALE, "external_id": external_id},
        headers=AUTH,
    )


@pytest.mark.anyio
async def test_dispatch_links_the_device(
    client: TestClient, session: AsyncSession, members: tuple[str, str]
) -> None:
    """an active mapping is created for that device to that Member."""
    member_a, _ = members

    response = _register(client, member_a, "SM-DISPATCH-001")

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == member_a
    assert body["identifier_type"] == SCALE
    assert body["superseded_user_id"] is None

    mapping = (
        await session.execute(select(UserExternalId).where(UserExternalId.id == body["id"]))
    ).scalar_one()
    assert mapping.ended_at is None


@pytest.mark.anyio
async def test_supersede_ends_the_old_mapping(
    client: TestClient, session: AsyncSession, members: tuple[str, str]
) -> None:
    """the old mapping is ended (kept for audit); the new one is active."""
    member_a, member_b = members
    first = _register(client, member_a, "SM-SUPERSEDE-001").json()

    response = _register(client, member_b, "SM-SUPERSEDE-001")

    assert response.status_code == 201
    body = response.json()
    assert body["superseded_user_id"] == member_a

    old = (
        await session.execute(select(UserExternalId).where(UserExternalId.id == first["id"]))
    ).scalar_one()
    assert old.ended_at is not None  # ended, not deleted — audit trail survives
    assert old.user_id == member_a

    new = (
        await session.execute(select(UserExternalId).where(UserExternalId.id == body["id"]))
    ).scalar_one()
    assert new.ended_at is None
    assert new.user_id == member_b


@pytest.mark.anyio
async def test_same_member_re_registration_is_idempotent(
    client: TestClient, members: tuple[str, str]
) -> None:
    """An at-least-once dispatch caller retrying must not flip-flop the audit trail."""
    member_a, _ = members
    first = _register(client, member_a, "SM-IDEMPOTENT-001").json()

    again = _register(client, member_a, "SM-IDEMPOTENT-001")

    assert again.status_code == 201
    assert again.json()["id"] == first["id"]  # the existing active mapping, unchanged


@pytest.mark.anyio
async def test_unauthenticated_registration_rejected(
    client: TestClient, session: AsyncSession, members: tuple[str, str]
) -> None:
    """rejected, and no mapping is created or changed."""
    member_a, _ = members

    response = client.post(
        "/v1/device-registrations",
        json={"user_id": member_a, "identifier_type": SCALE, "external_id": "SM-NOAUTH-001"},
    )

    assert response.status_code == 401
    rows = (
        (
            await session.execute(
                select(UserExternalId).where(UserExternalId.external_id == "SM-NOAUTH-001")
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


def test_unknown_identifier_type_rejected(client: TestClient) -> None:
    """Registration with an unknown identifier type is rejected with 422."""
    response = client.post(
        "/v1/device-registrations",
        json={"user_id": "whoever", "identifier_type": "NHS Number", "external_id": "999"},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_unknown_member_rejected(client: TestClient) -> None:
    """Registration against a member profile that does not exist is rejected with 404."""
    response = client.post(
        "/v1/device-registrations",
        json={"user_id": "no-such-profile", "identifier_type": SCALE, "external_id": "SM-X"},
        headers=AUTH,
    )
    assert response.status_code == 404


def test_connect_holds_no_direct_write_path_to_kernel_identifiers() -> None:
    """/ registration writes go through the identity seam
    (``strata_identity.identifiers.adopt``) — this service constructs no
    ``UserExternalId`` row of its own, anywhere in its source."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(path.relative_to(app_dir.parent))
        for path in app_dir.rglob("*.py")
        if "UserExternalId(" in path.read_text()
    ]
    assert offenders == []
