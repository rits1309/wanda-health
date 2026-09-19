"""The demographics write seam against a migrated ephemeral Postgres."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.clinical import MemberDemographics
from strata_core.domains.kernel import UserProfile

from strata_member import converge_demographics

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _member(session: AsyncSession, sub: str = "m-1") -> str:
    """Create a real member profile (the clinical FK target) and return its id."""
    profile = UserProfile(
        email=f"{sub}@example.test",
        first_name="Theo",
        last_name="Van Heusen",
        timezone="Europe/London",
    )
    session.add(profile)
    await session.flush()
    return profile.id


async def test_converge_demographics_creates_the_row(session: AsyncSession) -> None:
    """A first converge writes the one-to-one demographics row keyed on member_user_id."""
    member_user_id = await _member(session)
    row = await converge_demographics(
        session,
        member_user_id=member_user_id,
        sex="male",
        date_of_birth=date(1978, 1, 17),
        ethnicity="white",
        marital_status="U",  # legacy single-letter value, stored as recorded
        height_in=Decimal("65"),
    )
    assert row.member_user_id == member_user_id
    assert row.marital_status == "U"
    assert row.height_in == Decimal("65")


async def test_converge_demographics_is_idempotent(session: AsyncSession) -> None:
    """Re-converging the same user updates in place — one row, not a duplicate."""
    member_user_id = await _member(session)
    await converge_demographics(session, member_user_id=member_user_id, sex="male")
    await converge_demographics(
        session, member_user_id=member_user_id, sex="female", height_in=Decimal("70")
    )

    count = (
        await session.execute(
            select(func.count())
            .select_from(MemberDemographics)
            .where(MemberDemographics.member_user_id == member_user_id)
        )
    ).scalar_one()
    assert count == 1
    row = await session.get(MemberDemographics, member_user_id)
    assert row is not None
    assert row.sex == "female"  # the second converge won
    assert row.height_in == Decimal("70")


async def test_converge_demographics_requires_a_member_user_id(session: AsyncSession) -> None:
    """A blank member_user_id is refused before any write (never a guessed / orphan row)."""
    with pytest.raises(ValueError, match="member_user_id is required"):
        await converge_demographics(session, member_user_id="   ")


async def test_converge_demographics_rejects_an_unknown_member(session: AsyncSession) -> None:
    """An unknown member_user_id fails at flush on the user_profiles FK, never a silent orphan."""
    with pytest.raises(IntegrityError):
        await converge_demographics(session, member_user_id="no-such-profile", sex="male")
