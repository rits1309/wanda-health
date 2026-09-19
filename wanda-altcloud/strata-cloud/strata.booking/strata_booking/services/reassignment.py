"""Same-time coach reassignment: the appointment's time and member never change.

Options are other programme coaches with an ``available`` slot at the exact original start and
duration, honouring the **original** booking's language criteria (the snapshot taken at booking
time, not the member's current preference). An empty list means reassignment is unavailable.
Reassignment moves the *same* appointment record to the new coach/slot — unlike rescheduling it
creates no successor; the hop is audited in ``reassignment_events``. The member is notified only
when the programme's ``reassignment_notification_enabled`` config says so.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from strata_core.domains.booking import Appointment, ReassignmentEvent, Reminder, Slot
from strata_core.domains.kernel import Programme, ProgrammeAssignment, UserProfile

from strata_booking.core.clock import clock
from strata_booking.core.security import Principal
from strata_booking.schemas.booking import ReassignmentOption
from strata_booking.services.notifications import get_sender
from strata_booking.services.outbox import emit


async def _candidate_coaches(
    session: AsyncSession, appointment: Appointment
) -> dict[str, UserProfile]:
    """Other programme coaches, filtered by the original booking's language criteria."""
    coach_ids = set(
        (
            await session.execute(
                select(ProgrammeAssignment.user_id).where(
                    ProgrammeAssignment.programme_id == appointment.programme_id,
                    ProgrammeAssignment.assigned_as("Coach"),
                )
            )
        )
        .scalars()
        .all()
    ) - {appointment.coach_id}
    if not coach_ids:
        return {}
    coaches = (
        (
            await session.execute(
                select(UserProfile)
                .options(selectinload(UserProfile.user_languages))
                .where(UserProfile.id.in_(coach_ids))
            )
        )
        .scalars()
        .all()
    )
    if appointment.booking_language_matched:
        preferred = appointment.booking_preferred_language
        coaches = [c for c in coaches if preferred in c.language_codes]
    return {c.id: c for c in coaches}


def _require_confirmed(appointment: Appointment) -> None:
    if appointment.status != "confirmed":
        raise HTTPException(
            status_code=409, detail=f"Appointment is {appointment.status}, not reassignable"
        )


async def reassignment_options(
    session: AsyncSession, appointment: Appointment
) -> list[ReassignmentOption]:
    """Slots of other coaches at the exact original start/duration; empty ⇒ unavailable."""
    _require_confirmed(appointment)
    original = await session.get(Slot, appointment.slot_id)
    if original is None:
        raise HTTPException(status_code=500, detail="Appointment slot missing")
    coaches = await _candidate_coaches(session, appointment)
    if not coaches:
        return []
    slots = (
        (
            await session.execute(
                select(Slot)
                .where(
                    Slot.coach_id.in_(list(coaches)),
                    Slot.status == "available",
                    Slot.start_utc == original.start_utc,
                    Slot.duration_minutes == original.duration_minutes,
                )
                .order_by(Slot.coach_id)
            )
        )
        .scalars()
        .all()
    )
    return [
        ReassignmentOption(
            slot_id=s.id,
            coach_id=s.coach_id,
            coach_name=coaches[s.coach_id].effective_display_name,
            coach_languages=coaches[s.coach_id].language_codes,
        )
        for s in slots
    ]


async def reassign(
    session: AsyncSession,
    principal: Principal,
    appointment: Appointment,
    *,
    new_slot_id: str,
    on_behalf_of_coach_id: str | None,
) -> None:
    """Move the appointment to the selected coach's same-time slot; commits."""
    _require_confirmed(appointment)

    rows = (
        (
            await session.execute(
                select(Slot)
                .where(Slot.id.in_([appointment.slot_id, new_slot_id]))
                .order_by(Slot.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    slots = {s.id: s for s in rows}
    old_slot = slots.get(appointment.slot_id)
    new_slot = slots.get(new_slot_id)
    if old_slot is None:
        raise HTTPException(status_code=500, detail="Appointment slot missing")
    if new_slot is None:
        raise HTTPException(status_code=404, detail="Slot not found")
    if new_slot.status != "available":
        raise HTTPException(status_code=409, detail="Slot is no longer available")
    if new_slot.start_utc <= clock.now():
        raise HTTPException(status_code=409, detail="Slot is in the past")
    if (
        new_slot.start_utc != old_slot.start_utc
        or new_slot.duration_minutes != old_slot.duration_minutes
    ):
        raise HTTPException(
            status_code=422,
            detail="Reassignment keeps the exact time — pick a slot at the same start/duration",
        )
    coaches = await _candidate_coaches(session, appointment)
    if new_slot.coach_id not in coaches:
        raise HTTPException(
            status_code=422,
            detail="That coach is not eligible — must be another programme coach matching the "
            "original booking criteria",
        )

    from_coach_id = appointment.coach_id
    old_slot.status = "available"
    new_slot.status = "booked"
    appointment.coach_id = new_slot.coach_id
    appointment.slot_id = new_slot.id

    session.add(
        ReassignmentEvent(
            appointment_id=appointment.id,
            from_coach_id=from_coach_id,
            to_coach_id=new_slot.coach_id,
            from_slot_id=old_slot.id,
            to_slot_id=new_slot.id,
            initiating_user_id=principal.sub,
            on_behalf_of_coach_id=on_behalf_of_coach_id,
            occurred_at=clock.now(),
        )
    )
    # The pending coach reminder follows the appointment to the new coach; member's unchanged.
    coach_reminders = (
        (
            await session.execute(
                select(Reminder).where(
                    Reminder.appointment_id == appointment.id,
                    Reminder.recipient_kind == "coach",
                    Reminder.status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )
    for reminder in coach_reminders:
        reminder.recipient_id = new_slot.coach_id

    programme = await session.get(Programme, appointment.programme_id)
    member_notified = bool(programme.reassignment_notification_enabled if programme else True)
    if member_notified:
        await get_sender().send(
            session,
            channel="email",
            recipient_kind="member",
            recipient_id=appointment.member_id,
            notification_type="appointment_reassigned",
            payload={
                "appointment_id": appointment.id,
                "from_coach_id": from_coach_id,
                "to_coach_id": new_slot.coach_id,
                "start_utc": new_slot.start_utc.isoformat(),
            },
        )
    emit(
        session,
        "AppointmentReassigned",
        {
            "appointment_id": appointment.id,
            "member_id": appointment.member_id,
            "from_coach_id": from_coach_id,
            "to_coach_id": new_slot.coach_id,
            "from_slot_id": old_slot.id,
            "to_slot_id": new_slot.id,
            "member_notified": member_notified,
            "acting_user_id": principal.sub,
            "on_behalf_of_coach_id": on_behalf_of_coach_id,
        },
    )
    await session.commit()
