"""Unavailability management: one-off blocks and recurring patterns.

Unavailability **overrides** availability. Creating it retro-marks overlapping future slots
``unavailable``; a booked overlapping slot is handled exactly as availability reshaping
via :func:`availability.cancel_booked_slot`. Removing it restores affected slots to
``available`` — unless another window still covers them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Slot, UnavailabilityBlock, UnavailabilityPattern

from strata_booking.core.clock import clock
from strata_booking.schemas.unavailability import BlockBody, RecurringBody
from strata_booking.services.authz import ActingContext
from strata_booking.services.availability import (
    _booked_appointment,
    cancel_booked_slot,
    observe_slot_desync,
)
from strata_booking.services.outbox import emit
from strata_booking.services.slot_generation import overlaps, to_utc, unavailability_windows


async def _future_coach_slots(session: AsyncSession, coach_id: str) -> list[Slot]:
    return list(
        (
            await session.execute(
                select(Slot).where(Slot.coach_id == coach_id, Slot.start_utc > clock.now())
            )
        )
        .scalars()
        .all()
    )


async def _apply_new_windows(
    session: AsyncSession,
    actor: ActingContext,
    windows: list[tuple[datetime, datetime]],
) -> None:
    """Mark future slots overlapping the new unavailability; cancel booked ones.

    A booked-status slot with no confirmed appointment behind it is a lifecycle desync: it is
    still healed to ``unavailable``, but through :func:`observe_slot_desync` — never
    as a silent conversion.
    """
    for slot in await _future_coach_slots(session, actor.coach_id):
        if not overlaps(slot.start_utc, slot.end_utc, windows):
            continue
        if slot.status == "available":
            slot.status = "unavailable"
        elif slot.status == "booked":
            appointment = await _booked_appointment(session, slot.id)
            if appointment is not None:
                await cancel_booked_slot(
                    session, actor, slot, appointment, reason="coach_unavailable"
                )
            else:
                # The slot lies (no confirmed appointment behind it): the window makes
                # unavailable the right heal, but never silently.
                observe_slot_desync(
                    session,
                    actor,
                    slot,
                    healed_to="unavailable",
                    touchpoint="unavailability_windows",
                )
            slot.status = "unavailable"


async def _reevaluate_after_removal(session: AsyncSession, coach_id: str) -> None:
    """Restore ``unavailable`` slots that no remaining window covers."""
    now = clock.now()
    horizon_guess = now.date() + timedelta(weeks=52)
    remaining = await unavailability_windows(session, coach_id, now.date(), horizon_guess)
    for slot in await _future_coach_slots(session, coach_id):
        if slot.status == "unavailable" and not overlaps(slot.start_utc, slot.end_utc, remaining):
            slot.status = "available"


def _attribution(actor: ActingContext) -> dict[str, object]:
    return {
        "acting_user_id": actor.acting_user_id,
        "on_behalf_of_coach_id": actor.on_behalf_of_coach_id,
    }


async def create_block(
    session: AsyncSession, actor: ActingContext, body: BlockBody
) -> UnavailabilityBlock:
    block = UnavailabilityBlock(
        coach_id=actor.coach_id,
        created_by=actor.acting_user_id,
        on_behalf_of_coach_id=actor.on_behalf_of_coach_id,
        **body.model_dump(),
    )
    session.add(block)
    await session.flush()
    window = (
        to_utc(block.date, block.start_time_local, block.timezone),
        to_utc(block.date, block.end_time_local, block.timezone),
    )
    await _apply_new_windows(session, actor, [window])
    emit(
        session,
        "UnavailabilityCreated",
        {"unavailability_id": block.id, "kind": "one_off", "coach_id": actor.coach_id}
        | _attribution(actor),
    )
    await session.commit()
    return block


async def remove_block(session: AsyncSession, actor: ActingContext, block_id: str) -> None:
    block = await session.get(UnavailabilityBlock, block_id)
    if block is None:
        raise HTTPException(status_code=404, detail="Unavailability block not found")
    if block.coach_id != actor.coach_id:
        raise HTTPException(status_code=403, detail="Unavailability belongs to another coach")
    await session.delete(block)
    await session.flush()
    await _reevaluate_after_removal(session, actor.coach_id)
    emit(
        session,
        "UnavailabilityRemoved",
        {"unavailability_id": block_id, "kind": "one_off", "coach_id": actor.coach_id}
        | _attribution(actor),
    )
    await session.commit()


async def list_blocks(session: AsyncSession, coach_id: str) -> list[UnavailabilityBlock]:
    return list(
        (
            await session.execute(
                select(UnavailabilityBlock)
                .where(UnavailabilityBlock.coach_id == coach_id)
                .order_by(UnavailabilityBlock.date)
            )
        )
        .scalars()
        .all()
    )


async def create_recurring(
    session: AsyncSession, actor: ActingContext, body: RecurringBody
) -> UnavailabilityPattern:
    pattern = UnavailabilityPattern(
        coach_id=actor.coach_id,
        created_by=actor.acting_user_id,
        on_behalf_of_coach_id=actor.on_behalf_of_coach_id,
        **body.model_dump(),
    )
    session.add(pattern)
    await session.flush()
    windows: list[tuple[datetime, datetime]] = []
    day: date = max(clock.now().date(), pattern.active_from)
    while day <= pattern.active_to:
        if day.weekday() in pattern.days_of_week:
            windows.append(
                (
                    to_utc(day, pattern.start_time_local, pattern.timezone),
                    to_utc(day, pattern.end_time_local, pattern.timezone),
                )
            )
        day += timedelta(days=1)
    await _apply_new_windows(session, actor, windows)
    emit(
        session,
        "UnavailabilityCreated",
        {"unavailability_id": pattern.id, "kind": "recurring", "coach_id": actor.coach_id}
        | _attribution(actor),
    )
    await session.commit()
    return pattern


async def remove_recurring(session: AsyncSession, actor: ActingContext, pattern_id: str) -> None:
    pattern = await session.get(UnavailabilityPattern, pattern_id)
    if pattern is None or pattern.status == "deleted":
        raise HTTPException(status_code=404, detail="Unavailability pattern not found")
    if pattern.coach_id != actor.coach_id:
        raise HTTPException(status_code=403, detail="Unavailability belongs to another coach")
    pattern.status = "deleted"
    await session.flush()
    await _reevaluate_after_removal(session, actor.coach_id)
    emit(
        session,
        "UnavailabilityRemoved",
        {"unavailability_id": pattern_id, "kind": "recurring", "coach_id": actor.coach_id}
        | _attribution(actor),
    )
    await session.commit()


async def list_recurring(session: AsyncSession, coach_id: str) -> list[UnavailabilityPattern]:
    return list(
        (
            await session.execute(
                select(UnavailabilityPattern)
                .where(
                    UnavailabilityPattern.coach_id == coach_id,
                    UnavailabilityPattern.status == "active",
                )
                .order_by(UnavailabilityPattern.created_at)
            )
        )
        .scalars()
        .all()
    )
