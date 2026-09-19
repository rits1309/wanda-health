"""Lazy rolling slot generation with unavailability precedence.

Pattern times are coach-local; every occurrence converts to UTC individually, so DST
discontinuity across a boundary is an accepted MVP trade-off. Generation
is **idempotent**: `(coach_id, start_utc, end_utc)` is unique and existing slot rows are left
untouched, so re-running for an overlapping window never duplicates or resets state. A slot
whose window overlaps any unavailability (one-off block or recurring pattern) is created as
``unavailable`` — unavailability always takes precedence.

The horizon is per-programme configuration (``slot_horizon_weeks``); a coach in several
programmes generates to the furthest horizon.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import CursorResult, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.db.common import new_id
from strata_core.domains.booking import (
    AvailabilityPattern,
    Slot,
    UnavailabilityBlock,
    UnavailabilityPattern,
)
from strata_core.domains.kernel import Programme, ProgrammeAssignment

from strata_booking.core.clock import clock

DEFAULT_HORIZON_WEEKS = 4


def to_utc(day: date, local_time: time, tz: str) -> datetime:
    """A coach-local wall-clock time on a given day, as UTC."""
    return datetime.combine(day, local_time, tzinfo=ZoneInfo(tz)).astimezone(UTC)


async def horizon_end(session: AsyncSession, coach_id: str) -> date:
    """The generation horizon for a coach: the furthest slot_horizon_weeks across programmes."""
    weeks = (
        await session.execute(
            select(func.max(Programme.slot_horizon_weeks))
            .join(ProgrammeAssignment, ProgrammeAssignment.programme_id == Programme.id)
            .where(
                ProgrammeAssignment.assigned_as("Coach"),
                ProgrammeAssignment.user_id == coach_id,
            )
        )
    ).scalar_one_or_none()
    return clock.now().date() + timedelta(weeks=weeks or DEFAULT_HORIZON_WEEKS)


async def unavailability_windows(
    session: AsyncSession, coach_id: str, from_date: date, to_date: date
) -> list[tuple[datetime, datetime]]:
    """All UTC unavailability windows for a coach across a date range (blocks + patterns)."""
    windows: list[tuple[datetime, datetime]] = []

    blocks = (
        (
            await session.execute(
                select(UnavailabilityBlock).where(
                    UnavailabilityBlock.coach_id == coach_id,
                    UnavailabilityBlock.date >= from_date,
                    UnavailabilityBlock.date <= to_date,
                )
            )
        )
        .scalars()
        .all()
    )
    for b in blocks:
        windows.append(
            (
                to_utc(b.date, b.start_time_local, b.timezone),
                to_utc(b.date, b.end_time_local, b.timezone),
            )
        )

    patterns = (
        (
            await session.execute(
                select(UnavailabilityPattern).where(
                    UnavailabilityPattern.coach_id == coach_id,
                    UnavailabilityPattern.status == "active",
                    UnavailabilityPattern.active_from <= to_date,
                    UnavailabilityPattern.active_to >= from_date,
                )
            )
        )
        .scalars()
        .all()
    )
    for p in patterns:
        day = max(from_date, p.active_from)
        last = min(to_date, p.active_to)
        while day <= last:
            if day.weekday() in p.days_of_week:
                windows.append(
                    (
                        to_utc(day, p.start_time_local, p.timezone),
                        to_utc(day, p.end_time_local, p.timezone),
                    )
                )
            day += timedelta(days=1)
    return windows


def occurrences(
    pattern: AvailabilityPattern, from_date: date, to_date: date
) -> Iterator[tuple[datetime, datetime]]:
    """Every slot (start_utc, end_utc) the pattern defines in the date range.

    Only whole slots that fit before the end time are generated — a 1-hour block with
    20-minute slots yields exactly 3.
    """
    tz = ZoneInfo(pattern.timezone)
    step = timedelta(minutes=pattern.slot_duration_minutes)
    day = max(from_date, pattern.active_from)
    last = min(to_date, pattern.active_to)
    while day <= last:
        if day.weekday() in pattern.days_of_week:
            start_local = datetime.combine(day, pattern.start_time_local, tzinfo=tz)
            end_local = datetime.combine(day, pattern.end_time_local, tzinfo=tz)
            s = start_local
            while s + step <= end_local:
                yield s.astimezone(UTC), (s + step).astimezone(UTC)
                s += step
        day += timedelta(days=1)


def overlaps(start: datetime, end: datetime, windows: list[tuple[datetime, datetime]]) -> bool:
    return any(start < w_end and w_start < end for w_start, w_end in windows)


async def generate_slots_for_pattern(
    session: AsyncSession, pattern: AvailabilityPattern, *, from_date: date, to_date: date
) -> int:
    """Materialise the pattern's slots for the window; returns how many rows were created.

    Existing rows (same coach/start/end) are untouched, making generation safely re-runnable.
    Advances the pattern's ``generation_watermark``.
    """
    windows = await unavailability_windows(session, pattern.coach_id, from_date, to_date)
    created = 0
    for start_utc, end_utc in occurrences(pattern, from_date, to_date):
        status = "unavailable" if overlaps(start_utc, end_utc, windows) else "available"
        stmt = (
            pg_insert(Slot)
            .values(
                id=new_id(),
                coach_id=pattern.coach_id,
                pattern_id=pattern.id,
                pattern_version=pattern.version,
                start_utc=start_utc,
                end_utc=end_utc,
                duration_minutes=pattern.slot_duration_minutes,
                status=status,
            )
            .on_conflict_do_update(
                index_elements=["coach_id", "start_utc", "end_utc"],
                set_={
                    "status": status,
                    "pattern_id": pattern.id,
                    "pattern_version": pattern.version,
                    "duration_minutes": pattern.slot_duration_minutes,
                },
                # Only a retired slot is revived; a live row at the same
                # coordinates — booked, available or unavailable — stays untouched.
                where=(Slot.status == "cancelled"),
            )
        )
        result = cast("CursorResult[object]", await session.execute(stmt))
        created += result.rowcount or 0
    if pattern.generation_watermark is None or to_date > pattern.generation_watermark:
        pattern.generation_watermark = to_date
    return created
