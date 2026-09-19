"""No-show handling: manual coach marking + system detection via call records.

System detection runs as a job: any **confirmed** appointment past ``start + grace`` (the
programme's ``no_show_grace_minutes``, default 15) is checked against the
:class:`CallRecordProvider` over ``[start − 5 min, now]`` — no call means ``no_show`` (both
parties notified, ``AppointmentNoShowDetected`` emitted); a call found marks the appointment
``completed``.

Two provisional readings, flagged for confirmation:

- "completed" transition: the requirements name the state but no actor; detection setting it when a
  call exists is the natural fit and stops the job rescanning forever.
- Detection considers ``confirmed`` records only, not ``rescheduled``: a
  frozen ``rescheduled`` record was superseded — its meeting never happened *by design*, and its
  chain successor is the confirmed record detection does examine. Marking predecessors
  ``no_show`` would corrupt the chain history.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, Slot
from strata_core.domains.kernel import Programme

from strata_booking.core.clock import clock
from strata_booking.core.security import Principal
from strata_booking.services.call_records import get_provider
from strata_booking.services.cancellation import cancel_pending_reminders
from strata_booking.services.notifications import get_sender
from strata_booking.services.outbox import emit

DEFAULT_GRACE_MINUTES = 15
_CALL_WINDOW_LEAD = timedelta(minutes=5)  # calls may start slightly before the slot


async def mark_no_show(
    session: AsyncSession,
    principal: Principal,
    appointment: Appointment,
    *,
    on_behalf_of_coach_id: str | None,
) -> None:
    """Coach/admin manual marking; commits. Emits ``AppointmentNoShowMarked``."""
    if appointment.status != "confirmed":
        raise HTTPException(
            status_code=409, detail=f"Appointment is {appointment.status}, not markable"
        )
    slot = await session.get(Slot, appointment.slot_id)
    if slot is None:
        raise HTTPException(status_code=500, detail="Appointment slot missing")
    now = clock.now()
    if slot.start_utc > now:
        raise HTTPException(status_code=409, detail="Appointment has not started yet")

    appointment.status = "no_show"
    appointment.no_show_detection = "coach"
    appointment.no_show_detected_at = now
    await cancel_pending_reminders(session, appointment.id)
    emit(
        session,
        "AppointmentNoShowMarked",
        {
            "appointment_id": appointment.id,
            "member_id": appointment.member_id,
            "coach_id": appointment.coach_id,
            "acting_user_id": principal.sub,
            "on_behalf_of_coach_id": on_behalf_of_coach_id,
            "detected_at": now.isoformat(),
        },
    )
    await session.commit()


async def detect_no_shows(session: AsyncSession) -> dict[str, int]:
    """System detection job; commits. Returns counts."""
    now = clock.now()
    rows = (
        await session.execute(
            select(Appointment, Slot)
            .join(Slot, Slot.id == Appointment.slot_id)
            .where(Appointment.status == "confirmed", Slot.start_utc <= now)
            .order_by(Slot.start_utc)
        )
    ).all()
    grace_by_programme: dict[str, timedelta] = {}
    sender = get_sender()
    provider = get_provider()
    no_shows = completed = 0
    for appointment, slot in rows:
        if appointment.programme_id not in grace_by_programme:
            programme = await session.get(Programme, appointment.programme_id)
            grace_by_programme[appointment.programme_id] = timedelta(
                minutes=programme.no_show_grace_minutes if programme else DEFAULT_GRACE_MINUTES
            )
        if slot.start_utc + grace_by_programme[appointment.programme_id] > now:
            continue

        called = await provider.call_initiated(
            session,
            coach_id=appointment.coach_id,
            member_id=appointment.member_id,
            window_start=slot.start_utc - _CALL_WINDOW_LEAD,
            window_end=now,
        )
        if called:
            appointment.status = "completed"  # provisional reading — see module docstring
            completed += 1
            continue

        appointment.status = "no_show"
        appointment.no_show_detection = "system"
        appointment.no_show_detected_at = now
        await cancel_pending_reminders(session, appointment.id)
        for kind, recipient in (
            ("member", appointment.member_id),
            ("coach", appointment.coach_id),
        ):
            await sender.send(
                session,
                channel="email",
                recipient_kind="coach" if kind == "coach" else "member",
                recipient_id=recipient,
                notification_type="appointment_no_show",
                payload={
                    "appointment_id": appointment.id,
                    "coach_id": appointment.coach_id,
                    "member_id": appointment.member_id,
                    "start_utc": slot.start_utc.isoformat(),
                },
            )
        emit(
            session,
            "AppointmentNoShowDetected",
            {
                "appointment_id": appointment.id,
                "member_id": appointment.member_id,
                "coach_id": appointment.coach_id,
                "detected_at": now.isoformat(),
            },
        )
        no_shows += 1
    await session.commit()
    return {"no_shows_detected": no_shows, "appointments_completed": completed}
