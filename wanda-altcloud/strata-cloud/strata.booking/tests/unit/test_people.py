"""People-seam listing tests: remit boundaries the seeded cast cannot express.

Every seeded coach belongs to p-1 (which Dana administers), so the API suite
cannot stage a coach wholly outside a scoped admin's remit, nor two members
sharing a display name. These tests flush the missing rows into the test
session directly (discarded on close) and drive the service seam.
"""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import LEGACY_SUMMIT_DJANGO_USER_ID
from strata_core.fixtures.booking_data import converge_migrated_member_assignments
from strata_core.fixtures.factories import assign_role, register_device

from strata_booking.services.people import display_names_for, list_members
from tests.auth import as_principal
from tests.identity_stubs import Coach, Member, ProgrammeAssignment


@pytest.mark.anyio
async def test_admin_cannot_act_for_a_coach_sharing_no_administered_programme(
    session: AsyncSession,
) -> None:
    """An admin naming a coach who shares no administered programme is refused with 403."""
    session.add(Coach(id="c-p2-solo", display_name="Pat Solo"))
    session.add(ProgrammeAssignment(programme_id="p-2", user_kind="coach", user_id="c-p2-solo"))
    await session.flush()
    with pytest.raises(HTTPException) as err:
        # Dana administers p-1 only; this coach exists only in p-2.
        await list_members(session, as_principal("c-2", "admin"), coach_id="c-p2-solo")
    assert err.value.status_code == 403


@pytest.mark.anyio
async def test_acting_for_a_coach_narrows_to_that_coachs_programmes(session: AsyncSession) -> None:
    """An all-programme admin acting for a p-2-only coach receives p-2's members only."""
    session.add(Coach(id="c-p2-lone", display_name="Pat Lone"))
    session.add(ProgrammeAssignment(programme_id="p-2", user_kind="coach", user_id="c-p2-lone"))
    await session.flush()
    members = await list_members(session, as_principal("a-1", "admin"), coach_id="c-p2-lone")
    assert [m.id for m in members] == ["m-5"]


@pytest.mark.anyio
async def test_converged_migrated_member_appears_in_the_listing(session: AsyncSession) -> None:
    """A migrated member, once converged, shows up in a p-1 coach's listing."""
    profile = Member(id="mig-listing", display_name="Theo VH")
    session.add(profile)
    await session.flush()
    await assign_role(session, profile, "Member")
    await register_device(
        session,
        user_id="mig-listing",
        type_name=LEGACY_SUMMIT_DJANGO_USER_ID,
        external_id="9001",
        registered_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    # The convergence function is driven directly; the seed WIRING (the
    # booking-acceptance loader calling it) is proven in strata.core's
    # test_booking_acceptance_converges_migrated_member_assignments.
    await converge_migrated_member_assignments(session)
    members = await list_members(session, as_principal("c-1", "coach"))
    assert [m.display_name for m in members] == [
        "Luis Ortega",
        "Marta Iglesias",  # the seeded migrated stand-in (m-6)
        "Morgan Lee",
        "Sam",  # Sam Carter's cast override
        "Sam Nguyen",
        "Theo VH",
    ]


@pytest.mark.anyio
async def test_display_names_skip_unknown_ids(session: AsyncSession) -> None:
    """An unknown profile id is simply absent from the map — callers degrade to the raw id."""
    names = await display_names_for(session, {"m-1", "ghost-profile"})
    assert names == {"m-1": "Morgan Lee"}
    assert names.get("ghost-profile", "ghost-profile") == "ghost-profile"  # the caller pattern


@pytest.mark.anyio
async def test_ordering_ties_break_on_id(session: AsyncSession) -> None:
    """Two members sharing a display name order by id — the stable-picker tie-break."""
    session.add(Member(id="dup-b", display_name="Alex Same"))
    session.add(Member(id="dup-a", display_name="Alex Same"))
    session.add(Coach(id="c-tie", display_name="Tie Coach"))
    for user_kind, user_id in (("member", "dup-b"), ("member", "dup-a"), ("coach", "c-tie")):
        session.add(ProgrammeAssignment(programme_id="p-2", user_kind=user_kind, user_id=user_id))
    await session.flush()
    members = await list_members(session, as_principal("c-tie", "coach"))
    assert [m.id for m in members] == ["dup-a", "dup-b", "m-5"]
