"""The coach calendar: pattern visibility, appointment visibility, and the admin layers.

Returns every slot in the window with its appointment attached when booked — live
(``confirmed``) or terminal (``no_show``/``completed``), never a freed
``cancelled``/``rescheduled`` record. All times are UTC; the client displays them in the
viewer's timezone.
"""

from datetime import UTC, date, datetime, time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, Slot
from strata_core.domains.kernel import ProgrammeAssignment

from strata_booking.core.security import Principal
from strata_booking.schemas.unavailability import (
    AdminCalendar,
    CalendarAppointment,
    CalendarEntry,
    CoachCalendar,
)
from strata_booking.services.authz import administered_programme_ids
from strata_booking.services.people import display_names_for

ALL_LAYERS = ("available", "booked", "unavailable")


async def coach_calendar(
    session: AsyncSession, coach_id: str, from_date: date, to_date: date
) -> CoachCalendar:
    window_start = datetime.combine(from_date, time.min, tzinfo=UTC)
    window_end = datetime.combine(to_date, time.max, tzinfo=UTC)
    rows = (
        (
            await session.execute(
                select(Slot, Appointment)
                .join(
                    Appointment,
                    # Live and terminal states attach: a no-show/completed
                    # appointment leaves its slot booked forever, so the calendar keeps
                    # its identity and outcome. Exactly these three — cancelled and
                    # rescheduled records belong to freed/re-offered slots and would
                    # render ghosts (incl. the rebook-after-cancel case).
                    (Appointment.slot_id == Slot.id)
                    & (Appointment.status.in_(("confirmed", "no_show", "completed"))),
                    isouter=True,
                )
                .where(
                    Slot.coach_id == coach_id,
                    # Retired slots are lifecycle records, not calendar entries
                    # only the declared layers ever reach a client.
                    Slot.status.in_(ALL_LAYERS),
                    Slot.start_utc >= window_start,
                    Slot.start_utc <= window_end,
                )
                .order_by(Slot.start_utc)
            )
        )
        .tuples()
        .all()
    )
    # One batched lookup covers the coach and every booked member (3.2);
    # a dangling id degrades to the raw identifier, never an error.
    names = await display_names_for(
        session,
        {coach_id} | {appt.member_id for _, appt in rows if appt is not None},
    )
    entries = [
        CalendarEntry(
            slot_id=slot.id,
            coach_id=slot.coach_id,
            start_utc=slot.start_utc,
            end_utc=slot.end_utc,
            duration_minutes=slot.duration_minutes,
            status=slot.status,
            appointment=(
                CalendarAppointment(
                    id=appointment.id,
                    member_id=appointment.member_id,
                    member_name=names.get(appointment.member_id, appointment.member_id),
                    status=appointment.status,
                )
                if appointment is not None
                else None
            ),
        )
        for slot, appointment in rows
    ]
    return CoachCalendar(
        coach_id=coach_id,
        coach_name=names.get(coach_id, coach_id),
        from_date=from_date,
        to_date=to_date,
        entries=entries,
    )


async def admin_calendar(
    session: AsyncSession,
    principal: Principal,
    *,
    coach_ids: list[str] | None,
    from_date: date,
    to_date: date,
    layers: list[str] | None,
) -> AdminCalendar:
    """Combined calendar across the admin's programme coaches.

    ``coach_ids`` filters the coach set (default: every coach in the admin's programmes); any
    coach outside those programmes is a 403. ``layers`` toggles slot layers (default: all).
    The admin's programmes come from the kernel assignments, not token claims.
    """
    programme_ids = await administered_programme_ids(session, principal)
    administered = sorted(
        set(
            (
                await session.execute(
                    select(ProgrammeAssignment.user_id).where(
                        ProgrammeAssignment.programme_id.in_(sorted(programme_ids)),
                        ProgrammeAssignment.assigned_as("Coach"),
                    )
                )
            )
            .scalars()
            .all()
        )
    )
    selected = coach_ids if coach_ids else administered
    outside = sorted(set(selected) - set(administered))
    if outside:
        raise HTTPException(
            status_code=403,
            detail=f"Coaches outside your programmes: {', '.join(outside)}",
        )
    shown = list(layers) if layers else list(ALL_LAYERS)
    coaches: list[CoachCalendar] = []
    for coach_id in selected:
        full = await coach_calendar(session, coach_id, from_date, to_date)
        full.entries = [e for e in full.entries if e.status in shown]
        coaches.append(full)
    return AdminCalendar(from_date=from_date, to_date=to_date, layers=shown, coaches=coaches)
