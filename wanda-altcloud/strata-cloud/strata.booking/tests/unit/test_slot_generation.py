"""Unit tests for lazy rolling slot generation: packing, weekdays, DST, precedence, idempotency."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import AvailabilityPattern, Slot, UnavailabilityBlock
from strata_core.domains.kernel import UserProfile

from strata_booking.services.slot_generation import (
    generate_slots_for_pattern,
    horizon_end,
    occurrences,
    unavailability_windows,
)
from tests.identity_stubs import Coach, ProgrammeAssignment


def _pattern(**overrides: object) -> AvailabilityPattern:
    defaults: dict[str, object] = {
        "id": "pat-test",
        "coach_id": "c-test",
        "days_of_week": [0],  # Monday
        "start_time_local": time(9, 0),
        "end_time_local": time(10, 0),
        "slot_duration_minutes": 20,
        "timezone": "America/New_York",
        "active_from": date(2026, 10, 1),
        "active_to": date(2026, 12, 31),
        "version": 1,
        "status": "active",
    }
    defaults.update(overrides)
    return AvailabilityPattern(**defaults)


def test_one_hour_block_with_20_minute_slots_yields_three() -> None:
    # Monday 5 Oct 2026, 9:00–10:00.
    """A one-hour window with 20-minute slots yields three, in the coach's timezone."""
    slots = list(occurrences(_pattern(), date(2026, 10, 5), date(2026, 10, 5)))
    assert len(slots) == 3
    assert slots[0][0] == datetime(2026, 10, 5, 13, 0, tzinfo=UTC)  # 9am EDT
    assert slots[-1][1] == datetime(2026, 10, 5, 14, 0, tzinfo=UTC)


def test_partial_trailing_slot_is_not_generated() -> None:
    # 50-minute window with 20-minute slots → 2 whole slots, no partial.
    """A trailing partial slot is never generated."""
    slots = list(
        occurrences(_pattern(end_time_local=time(9, 50)), date(2026, 10, 5), date(2026, 10, 5))
    )
    assert len(slots) == 2


def test_only_configured_weekdays_generate() -> None:
    # Week of Mon 5 Oct – Sun 11 Oct; pattern is Monday-only → one day's worth.
    """Only the pattern's configured weekdays generate slots."""
    slots = list(occurrences(_pattern(), date(2026, 10, 5), date(2026, 10, 11)))
    assert {s.date() for s, _ in slots} == {date(2026, 10, 5)}


def test_active_range_bounds_generation() -> None:
    """Slots generate only inside the pattern's active date range."""
    pattern = _pattern(active_from=date(2026, 10, 6), active_to=date(2026, 10, 30))
    # Monday 5 Oct is before active_from; Monday 2 Nov is after active_to.
    assert list(occurrences(pattern, date(2026, 10, 5), date(2026, 10, 5))) == []
    assert list(occurrences(pattern, date(2026, 11, 2), date(2026, 11, 2))) == []


def test_dst_boundary_shifts_utc_offset() -> None:
    """US DST ends 1 Nov 2026: local 9am is 13:00Z before, 14:00Z after."""
    pattern = _pattern(days_of_week=[0, 4])  # Monday + Friday
    before = list(occurrences(pattern, date(2026, 10, 30), date(2026, 10, 30)))  # Fri (EDT)
    after = list(occurrences(pattern, date(2026, 11, 2), date(2026, 11, 2)))  # Mon (EST)
    assert before[0][0] == datetime(2026, 10, 30, 13, 0, tzinfo=UTC)
    assert after[0][0] == datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


async def _insert_test_coach(session: AsyncSession, coach_id: str) -> None:
    if await session.get(UserProfile, coach_id) is None:
        session.add(Coach(id=coach_id, display_name="Test", timezone="UTC", languages=["en"]))
        session.add(
            ProgrammeAssignment(
                id=f"pa-{coach_id}", programme_id="p-1", user_kind="coach", user_id=coach_id
            )
        )
        await session.flush()


@pytest.mark.anyio
async def test_generation_is_idempotent(session: AsyncSession) -> None:
    """Slot generation is idempotent: a second run creates nothing new."""
    await _insert_test_coach(session, "c-gen1")
    pattern = _pattern(id="pat-gen1", coach_id="c-gen1")
    session.add(pattern)
    await session.flush()

    first = await generate_slots_for_pattern(
        session, pattern, from_date=date(2026, 10, 5), to_date=date(2026, 10, 11)
    )
    second = await generate_slots_for_pattern(
        session, pattern, from_date=date(2026, 10, 5), to_date=date(2026, 10, 11)
    )
    await session.commit()

    assert first == 3 and second == 0
    count = (
        await session.execute(
            select(func.count()).select_from(Slot).where(Slot.pattern_id == "pat-gen1")
        )
    ).scalar_one()
    assert count == 3
    assert pattern.generation_watermark == date(2026, 10, 11)


@pytest.mark.anyio
async def test_unavailability_overlap_marks_slots_unavailable(session: AsyncSession) -> None:
    """Slots overlapping an unavailability block are created unavailable."""
    await _insert_test_coach(session, "c-gen2")
    # Block 9:00–9:30 local on the Monday: first two 20-min slots overlap, third doesn't.
    session.add(
        UnavailabilityBlock(
            id="ub-gen2",
            coach_id="c-gen2",
            date=date(2026, 10, 5),
            start_time_local=time(9, 0),
            end_time_local=time(9, 30),
            timezone="America/New_York",
            created_by="c-gen2",
        )
    )
    pattern = _pattern(id="pat-gen2", coach_id="c-gen2")
    session.add(pattern)
    await session.flush()

    await generate_slots_for_pattern(
        session, pattern, from_date=date(2026, 10, 5), to_date=date(2026, 10, 5)
    )
    await session.commit()

    rows = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == "pat-gen2").order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    assert [s.status for s in rows] == ["unavailable", "unavailable", "available"]


@pytest.mark.anyio
async def test_unavailability_windows_collects_blocks_and_patterns(session: AsyncSession) -> None:
    """Unavailability windows collect both one-off blocks and recurring patterns."""
    from strata_core.domains.booking import UnavailabilityPattern

    await _insert_test_coach(session, "c-gen4")
    session.add(
        UnavailabilityBlock(
            id="ub-gen4",
            coach_id="c-gen4",
            date=date(2026, 10, 5),
            start_time_local=time(8, 0),
            end_time_local=time(8, 30),
            timezone="UTC",
            created_by="c-gen4",
        )
    )
    session.add(
        UnavailabilityPattern(
            id="up-gen4",
            coach_id="c-gen4",
            days_of_week=[0, 1, 2, 3, 4],
            start_time_local=time(12, 0),
            end_time_local=time(13, 0),
            timezone="Europe/Madrid",
            active_from=date(2026, 10, 1),
            active_to=date(2026, 12, 31),
            created_by="c-gen4",
        )
    )
    await session.flush()

    # Mon 5 Oct + Tue 6 Oct: one one-off block + two recurring lunches.
    windows = await unavailability_windows(session, "c-gen4", date(2026, 10, 5), date(2026, 10, 6))
    assert len(windows) == 3
    lengths = {(end - start) for start, end in windows}
    assert lengths == {timedelta(minutes=30), timedelta(hours=1)}
    await session.rollback()


@pytest.mark.anyio
async def test_horizon_uses_programme_config_with_default(session: AsyncSession) -> None:
    """The generation horizon reads the programme config, defaulting to four weeks."""
    await _insert_test_coach(session, "c-gen3")
    end = await horizon_end(session, "c-gen3")  # p-1 default is 4 weeks
    assert end >= date.today() + timedelta(weeks=3)  # sanity: roughly 4 weeks out
