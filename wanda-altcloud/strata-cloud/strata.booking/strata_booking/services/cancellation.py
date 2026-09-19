"""Appointment cancellation and the coach-cancellation trigger.

Cancellation validates the effective policy (allowed-by + window), frees the slot, cancels
pending reminders, notifies the other party with the reason and next-step options, and emits
``AppointmentCancelled``. When the canceller is the coach (or an admin acting for one), the
member receives the suggested-alternatives email via the alternatives engine.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, Reminder, Slot

from strata_booking.core.security import ROLE_ADMIN, ROLE_COACH, ROLE_MEMBER, Principal
from strata_booking.services.alternatives import suggest_for_cancelled
from strata_booking.services.notifications import get_sender
from strata_booking.services.outbox import emit
from strata_booking.services.policy import validate_cancellation


async def cancel_pending_reminders(session: AsyncSession, appointment_id: str) -> None:
    await session.execute(
        update(Reminder)
        .where(Reminder.appointment_id == appointment_id, Reminder.status == "pending")
        .values(status="cancelled")
    )


async def cancel_appointment(
    session: AsyncSession,
    principal: Principal,
    appointment: Appointment,
    *,
    reason: str | None,
    on_behalf_of_coach_id: str | None,
) -> None:
    """Cancel a confirmed appointment per the effective policy; commits."""
    if appointment.status != "confirmed":
        raise HTTPException(
            status_code=409, detail=f"Appointment is {appointment.status}, not cancellable"
        )
    # The capacity in which the caller cancels (capability-based, never a role
    # collapse): the appointment's own member/coach cancels as themselves; an
    # admin cancels on the coach's side (recorded as "coach").
    if principal.has_role(ROLE_MEMBER) and appointment.member_id == principal.sub:
        kind = "member"
    elif principal.has_role(ROLE_COACH) and appointment.coach_id == principal.sub:
        kind = "coach"
    elif principal.has_role(ROLE_ADMIN):
        kind = "admin"
    else:
        raise HTTPException(status_code=403, detail="Insufficient role")
    await validate_cancellation(session, appointment, kind)
    cancelled_by = "coach" if kind == "admin" else kind

    slot = (
        await session.execute(select(Slot).where(Slot.id == appointment.slot_id).with_for_update())
    ).scalar_one()
    appointment.status = "cancelled"
    appointment.cancelled_by = cancelled_by
    appointment.cancellation_reason = reason
    slot.status = "available"  # the slot is freed for others
    # Release the member:slot idempotency key so the freed slot can be booked again by the
    # same member — otherwise book() replays this cancelled row (the reschedule precedent,
    # rescheduling.py)..
    appointment.idempotency_key = None
    await cancel_pending_reminders(session, appointment.id)

    emit(
        session,
        "AppointmentCancelled",
        {
            "appointment_id": appointment.id,
            "member_id": appointment.member_id,
            "coach_id": appointment.coach_id,
            "cancelled_by": cancelled_by,
            "reason": reason,
            "acting_user_id": principal.sub,
            "on_behalf_of_coach_id": on_behalf_of_coach_id,
        },
    )

    sender = get_sender()
    if cancelled_by == "member":
        # Notify the coach (reason if provided, plus options).
        await sender.send(
            session,
            channel="email",
            recipient_kind="coach",
            recipient_id=appointment.coach_id,
            notification_type="appointment_cancelled_by_member",
            payload={
                "appointment_id": appointment.id,
                "member_id": appointment.member_id,
                "reason": reason,
                "start_utc": slot.start_utc.isoformat(),
            },
        )
    else:
        # Coach/admin cancellation → the member's suggested-alternatives flow.
        await suggest_for_cancelled(
            session,
            appointment,
            slot,
            reason=reason or "coach_cancelled",
            acting_user_id=principal.sub,
            on_behalf_of_coach_id=on_behalf_of_coach_id,
        )
    await session.commit()
