"""The role-assignment seam (the database half of the sync rule) and the group vocabulary."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import UserProfile

from strata_identity.roles import (
    GROUP_BY_ROLE,
    ROLE_ADMIN,
    ROLE_BY_GROUP,
    ROLE_COACH,
    ROLE_NAMES,
    assign_role,
    revoke_role,
    roles_for,
)


def test_group_vocabulary_round_trips() -> None:
    """The Cognito group vocabulary round-trips with the role catalogue in both directions."""
    assert set(GROUP_BY_ROLE) == set(ROLE_NAMES)
    for role, group in GROUP_BY_ROLE.items():
        assert group == role.lower()
        assert ROLE_BY_GROUP[group] == role


@pytest.fixture
async def user_id(session: AsyncSession) -> str:
    # The role catalogue is already seeded by the canonical baseline migration.
    profile = UserProfile(
        email="user@wandahealth.com",
        first_name="Test",
        last_name="User",
        timezone="Europe/London",
    )
    session.add(profile)
    await session.flush()
    return profile.id


@pytest.mark.integration
@pytest.mark.anyio
async def test_assign_is_idempotent_and_readable(session: AsyncSession, user_id: str) -> None:
    """assign_role is idempotent and roles_for reads back the sorted role set."""
    assert await assign_role(session, user_id, ROLE_COACH) is True
    assert await assign_role(session, user_id, ROLE_COACH) is False
    assert await assign_role(session, user_id, ROLE_ADMIN) is True
    assert await roles_for(session, user_id) == ["Admin", "Coach"]


@pytest.mark.integration
@pytest.mark.anyio
async def test_revoke_is_idempotent(session: AsyncSession, user_id: str) -> None:
    """revoke_role is idempotent: revoking twice reports False the second time."""
    await assign_role(session, user_id, ROLE_COACH)
    assert await revoke_role(session, user_id, ROLE_COACH) is True
    assert await revoke_role(session, user_id, ROLE_COACH) is False
    assert await roles_for(session, user_id) == []


@pytest.mark.integration
@pytest.mark.anyio
async def test_unknown_role_is_rejected(session: AsyncSession, user_id: str) -> None:
    """Assigning a role outside the catalogue raises ValueError."""
    with pytest.raises(ValueError, match="Unknown role"):
        await assign_role(session, user_id, "Superuser")
