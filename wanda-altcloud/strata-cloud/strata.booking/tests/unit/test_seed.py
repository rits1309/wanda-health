"""The seed baseline is canonical and idempotent.

Booking's dataset is the ``booking-acceptance`` fixture profile from
``strata-core`` (loaded once by conftest). Loading it again must change
nothing, and the fixed walkthrough identities must exist under their
historical ids — people as kernel ``user_profiles`` rows.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from strata_core.domains.booking import Appointment, CancellationPolicy
from strata_core.domains.kernel import Programme, ProgrammeAssignment, UserProfile
from strata_core.fixtures import load_profile

from strata_booking.core.config import settings


async def _counts(session: AsyncSession) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, model in {
        "programmes": Programme,
        "people": UserProfile,
        "assignments": ProgrammeAssignment,
        "policies": CancellationPolicy,
        "appointments": Appointment,
    }.items():
        out[name] = (await session.execute(select(func.count()).select_from(model))).scalar_one()
    return out


@pytest.mark.anyio
async def test_profile_reload_is_stable(session: AsyncSession) -> None:
    """Reloading the booking-acceptance profile changes no row counts (idempotent)."""
    first = await _counts(session)

    # Deliberate exception to the isolated-session rule: idempotence of the
    # REAL loader against a real engine is the property under test, so this runs on its
    # own connections and its commits are permanent. Safe by construction — the loader's
    # writes converge toward the canonical seed, which is exactly what is asserted here.
    engine = create_async_engine(settings.database_url)
    try:
        await load_profile(engine, "booking-acceptance")  # conftest already loaded it once
    finally:
        await engine.dispose()

    second = await _counts(session)
    assert first == second

    # The fixed walkthrough identities exist (by id — safe regardless of other suites' activity).
    for model, key in [
        (Programme, "p-1"),
        (Programme, "p-2"),
        (UserProfile, "c-1"),
        (UserProfile, "c-3"),
        (UserProfile, "m-2"),
        (UserProfile, "m-3"),  # the booking-only extra
        (CancellationPolicy, "pol-c2-v1"),
        (Appointment, "seed-appt-0"),
    ]:
        assert await session.get(model, key) is not None, f"{model.__name__} {key} missing"


@pytest.mark.anyio
async def test_walkthrough_assignments_carry_kernel_roles(session: AsyncSession) -> None:
    """The walkthrough seed's programme assignments carry the kernel role names."""
    seeded_coach_assignments = (
        (
            await session.execute(
                select(ProgrammeAssignment.id).where(
                    ProgrammeAssignment.programme_id == "p-1",
                    ProgrammeAssignment.assigned_as("Coach"),
                    ProgrammeAssignment.id.like("pa-p-1-coach-%"),  # id-scoped: suite-order-safe
                )
            )
        )
        .scalars()
        .all()
    )
    assert sorted(seeded_coach_assignments) == [
        "pa-p-1-coach-c-1",
        "pa-p-1-coach-c-2",
        "pa-p-1-coach-c-3",
    ]
