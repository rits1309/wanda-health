"""Unavailability service unit tests: precedence, booked-slot cancellation, restoration."""

from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, OutboxEvent, Slot
from strata_core.domains.kernel import UserProfile

from strata_booking.core import clock as clock_module
from strata_booking.schemas.availability import PatternBody
from strata_booking.schemas.unavailability import BlockBody, RecurringBody
from strata_booking.services.authz import ActingContext
from strata_booking.services.availability import create_pattern
from strata_booking.services.unavailability import (
    create_block,
    create_recurring,
    remove_block,
    remove_recurring,
)
from tests.identity_stubs import Coach, ProgrammeAssignment

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW)


async def _coach_with_pattern(session: AsyncSession, coach_id: str) -> ActingContext:
    """A UTC coach with a Monday 9:00–10:00 pattern (20-min slots) in p-1."""
    if await session.get(UserProfile, coach_id) is None:
        session.add(Coach(id=coach_id, display_name="Un", timezone="UTC", languages=["en"]))
        session.add(
            ProgrammeAssignment(
                id=f"pa-{coach_id}", programme_id="p-1", user_kind="coach", user_id=coach_id
            )
        )
        await session.flush()
    actor = ActingContext(coach_id=coach_id, acting_user_id=coach_id, on_behalf_of_coach_id=None)
    await create_pattern(
        session,
        actor,
        PatternBody(
            days_of_week=[0],
            start_time_local=time(9, 0),
            end_time_local=time(10, 0),
            slot_duration_minutes=20,
            timezone="UTC",
            active_from=date(2026, 10, 1),
            active_to=date(2026, 12, 31),
        ),
    )
    return actor


async def _slots(session: AsyncSession, coach_id: str) -> list[Slot]:
    return list(
        (
            await session.execute(
                select(Slot).where(Slot.coach_id == coach_id).order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )


def _block(**overrides: object) -> BlockBody:
    defaults: dict[str, object] = {
        "date": date(2026, 10, 5),  # the first Monday
        "start_time_local": time(9, 20),
        "end_time_local": time(9, 40),
        "timezone": "UTC",
    }
    defaults.update(overrides)
    return BlockBody(**defaults)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_block_marks_overlap_unavailable_and_cancels_booked(session: AsyncSession) -> None:
    """A new block marks overlapping slots unavailable and cancels booked ones."""
    actor = await _coach_with_pattern(session, "c-un1")
    slots = await _slots(session, "c-un1")
    middle = slots[1]  # 9:20–9:40 on the first Monday
    middle.status = "booked"
    session.add(
        Appointment(
            id="appt-un1",
            slot_id=middle.id,
            coach_id="c-un1",
            member_id="m-1",
            programme_id="p-1",
            created_by="member",
            acting_user_id="m-1",
        )
    )
    await session.commit()

    await create_block(session, actor, _block())

    refreshed = await _slots(session, "c-un1")
    monday = [s for s in refreshed if s.start_utc.date() == date(2026, 10, 5)]
    assert [s.status for s in monday] == ["available", "unavailable", "available"]
    appt = await session.get(Appointment, "appt-un1")
    assert appt is not None and appt.status == "cancelled"
    events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "UnavailabilityCreated")
            )
        )
        .scalars()
        .all()
    )
    assert any(e.payload.get("coach_id") == "c-un1" for e in events)


@pytest.mark.anyio
async def test_block_over_desynced_booked_slot_heals_and_observes(session: AsyncSession) -> None:
    """A booked slot with no confirmed appointment is healed loudly, never silently.

    The shape cannot arise through normal use (booked/confirmed pairs are written in one
    transaction — family), so it is staged directly: a booked-status slot whose only
    appointment is already cancelled. The block still marks the slot unavailable, but the
    conversion emits ``SlotDesyncObserved`` and leaves the stale appointment untouched — no
    cancellation flow runs against a member who has no live booking.
    """
    actor = await _coach_with_pattern(session, "c-un5")
    middle = (await _slots(session, "c-un5"))[1]  # 9:20–9:40 on the first Monday
    middle.status = "booked"  # lies: its only appointment is already cancelled
    session.add(
        Appointment(
            id="appt-un5",
            slot_id=middle.id,
            coach_id="c-un5",
            member_id="m-1",
            programme_id="p-1",
            created_by="member",
            acting_user_id="m-1",
            status="cancelled",
        )
    )
    await session.commit()

    await create_block(session, actor, _block())

    monday = [s for s in await _slots(session, "c-un5") if s.start_utc.date() == date(2026, 10, 5)]
    assert [s.status for s in monday] == ["available", "unavailable", "available"]
    appt = await session.get(Appointment, "appt-un5")
    assert appt is not None and appt.status == "cancelled"  # untouched, not re-cancelled
    desync = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "SlotDesyncObserved")
            )
        )
        .scalars()
        .all()
    )
    assert [e.payload.get("slot_id") for e in desync] == [middle.id]
    assert desync[0].payload.get("healed_to") == "unavailable"
    assert desync[0].payload.get("acting_user_id") == "c-un5"
    cancelled = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentCancelled")
            )
        )
        .scalars()
        .all()
    )
    assert not cancelled  # nothing confirmed existed, so no cancellation was announced


@pytest.mark.anyio
async def test_remove_block_restores_availability(session: AsyncSession) -> None:
    """Removing a block restores the availability of the slots it covered."""
    actor = await _coach_with_pattern(session, "c-un2")
    block = await create_block(session, actor, _block())
    monday = [s for s in await _slots(session, "c-un2") if s.start_utc.date() == date(2026, 10, 5)]
    assert [s.status for s in monday] == ["available", "unavailable", "available"]

    await remove_block(session, actor, block.id)

    monday = [s for s in await _slots(session, "c-un2") if s.start_utc.date() == date(2026, 10, 5)]
    assert [s.status for s in monday] == ["available", "available", "available"]


@pytest.mark.anyio
async def test_recurring_marks_every_matching_day(session: AsyncSession) -> None:
    """A recurring pattern marks matching slots on every configured day."""
    actor = await _coach_with_pattern(session, "c-un3")
    await create_recurring(
        session,
        actor,
        RecurringBody(
            days_of_week=[0],
            start_time_local=time(9, 0),
            end_time_local=time(9, 20),
            timezone="UTC",
            active_from=date(2026, 10, 1),
            active_to=date(2026, 10, 31),
        ),
    )
    mondays_in_october = [
        s
        for s in await _slots(session, "c-un3")
        if s.start_utc.date().month == 10 and s.start_utc.time() == time(9, 0)
    ]
    assert mondays_in_october and all(s.status == "unavailable" for s in mondays_in_october)


@pytest.mark.anyio
async def test_removal_keeps_slots_still_covered_by_another_window(session: AsyncSession) -> None:
    """Removing one window keeps slots unavailable while another still covers them."""
    actor = await _coach_with_pattern(session, "c-un4")
    block = await create_block(
        session, actor, _block(start_time_local=time(9, 0), end_time_local=time(9, 20))
    )
    recurring = await create_recurring(
        session,
        actor,
        RecurringBody(
            days_of_week=[0],
            start_time_local=time(9, 0),
            end_time_local=time(9, 20),
            timezone="UTC",
            active_from=date(2026, 10, 1),
            active_to=date(2026, 10, 31),
        ),
    )

    await remove_block(session, actor, block.id)
    first_slot = next(
        s
        for s in await _slots(session, "c-un4")
        if s.start_utc == datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    )
    assert first_slot.status == "unavailable"  # recurring window still covers it

    await remove_recurring(session, actor, recurring.id)
    first_slot = next(
        s
        for s in await _slots(session, "c-un4")
        if s.start_utc == datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    )
    assert first_slot.status == "available"
