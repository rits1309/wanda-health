"""Rescheduling: member-fulfilled slot swap, coach-initiated request, history.

Each reschedule creates a **new** appointment record linked to its predecessor via
``original_appointment_id``; the superseded record is frozen as ``rescheduled`` and
its slot freed. A coach (or admin acting for one) never moves the appointment directly — they
*request*, the member fulfils via an emailed tokened link. Both paths validate the effective
policy window (the same window that governs cancellation). The new slot follows the same
matching rules as booking: a programme coach, and — when the original booking was
language-matched — one speaking the member's preferred language at booking time.

There is deliberately no ``RescheduleRequested`` event: a coach request is recorded in
``reschedule_tokens`` + the notification only; the eventual ``AppointmentRescheduled`` carries
the chain.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from strata_core.domains.booking import Appointment, RescheduleEvent, RescheduleToken, Slot
from strata_core.domains.kernel import Programme, UserProfile

from strata_booking.core.clock import clock
from strata_booking.core.security import Principal
from strata_booking.schemas.booking import (
    AppointmentHistoryOut,
    AppointmentOut,
    RescheduleEventOut,
    RescheduleLinkOut,
    RescheduleRequestOut,
)
from strata_booking.services import booking
from strata_booking.services.authz import programme_ids_for
from strata_booking.services.cancellation import cancel_pending_reminders
from strata_booking.services.notifications import get_sender
from strata_booking.services.outbox import emit
from strata_booking.services.policy import validate_window
from strata_booking.services.slot_generation import horizon_end
from strata_booking.services.tokens import (
    BOOKING_PAGE_LINK,
    hash_token,
    mint_link_token,
    verify_link_token,
)

_PURPOSE = "reschedule"
RESCHEDULE_PAGE_LINK = "/reschedule"  # client route carried in the member's link notification


async def reschedule_appointment(
    session: AsyncSession,
    principal: Principal,
    appointment: Appointment,
    *,
    new_slot_id: str,
    on_behalf_of_coach_id: str | None = None,
) -> AppointmentOut:
    """Move the appointment to a new slot; commits. Returns the successor record.

    Frees the old slot, freezes the old record as ``rescheduled``, links the successor via
    ``original_appointment_id``, records the hop in ``reschedule_events``, swaps the pending
    reminders over, notifies both parties, and emits ``AppointmentRescheduled`` — all in one
    transaction.
    """
    if appointment.status != "confirmed":
        raise HTTPException(
            status_code=409, detail=f"Appointment is {appointment.status}, not reschedulable"
        )
    if new_slot_id == appointment.slot_id:
        raise HTTPException(status_code=409, detail="That is the appointment's current slot")
    await validate_window(session, appointment, action="Reschedule")

    # Lock both slots in a stable order so concurrent swaps cannot deadlock.
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

    shared = sorted(
        await programme_ids_for(session, "member", appointment.member_id)
        & await programme_ids_for(session, "coach", new_slot.coach_id)
    )
    if not shared:
        raise HTTPException(
            status_code=403, detail="Coach is not available to the member's programme"
        )
    if appointment.booking_language_matched:
        coach = (
            await session.execute(
                select(UserProfile)
                .options(selectinload(UserProfile.user_languages))
                .where(UserProfile.id == new_slot.coach_id)
            )
        ).scalar_one_or_none()
        preferred = appointment.booking_preferred_language
        if coach is None or preferred not in coach.language_codes:
            raise HTTPException(
                status_code=422,
                detail="Selected slot does not match the booking's language preference",
            )

    key = f"{appointment.member_id}:{new_slot_id}"
    duplicate = (
        await session.execute(select(Appointment).where(Appointment.idempotency_key == key))
    ).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(
            status_code=409, detail="The member already has an appointment record for that slot"
        )

    old_slot.status = "available"
    new_slot.status = "booked"
    appointment.status = "rescheduled"
    # The frozen record's booking-retry key is spent: the member must be able to move back to
    # a previously-held slot later (the key column is UNIQUE for the row's lifetime).
    appointment.idempotency_key = None
    successor = Appointment(
        slot_id=new_slot.id,
        coach_id=new_slot.coach_id,
        member_id=appointment.member_id,
        programme_id=(
            appointment.programme_id if appointment.programme_id in shared else shared[0]
        ),
        created_by="member",  # rescheduling is always member-fulfilled
        acting_user_id=principal.sub,
        on_behalf_of_coach_id=on_behalf_of_coach_id,
        booking_language_matched=appointment.booking_language_matched,
        booking_preferred_language=appointment.booking_preferred_language,
        original_appointment_id=appointment.id,
        idempotency_key=key,
    )
    session.add(successor)
    await booking.flush_appointment_insert(session)

    session.add(
        RescheduleEvent(
            from_appointment_id=appointment.id,
            to_appointment_id=successor.id,
            initiated_by="member",
            acting_user_id=principal.sub,
            from_slot_id=old_slot.id,
            to_slot_id=new_slot.id,
            occurred_at=clock.now(),
        )
    )
    await cancel_pending_reminders(session, appointment.id)
    await booking.schedule_reminders(session, successor, new_slot)
    # Any open reschedule links for the superseded record are spent.
    await session.execute(
        update(RescheduleToken)
        .where(RescheduleToken.appointment_id == appointment.id, RescheduleToken.used_at.is_(None))
        .values(used_at=clock.now())
    )

    emit(
        session,
        "AppointmentRescheduled",
        {
            "from_appointment_id": appointment.id,
            "to_appointment_id": successor.id,
            "member_id": appointment.member_id,
            "from_coach_id": appointment.coach_id,
            "to_coach_id": successor.coach_id,
            "from_slot_id": old_slot.id,
            "to_slot_id": new_slot.id,
            "initiated_by": "member",
            "acting_user_id": principal.sub,
            "on_behalf_of_coach_id": on_behalf_of_coach_id,
        },
    )

    sender = get_sender()
    times = {
        "from_start_utc": old_slot.start_utc.isoformat(),
        "start_utc": new_slot.start_utc.isoformat(),
    }
    await sender.send(
        session,
        channel="email",
        recipient_kind="member",
        recipient_id=appointment.member_id,
        notification_type="appointment_rescheduled",
        payload={"appointment_id": successor.id, **times},
    )
    for coach_id in {appointment.coach_id, successor.coach_id}:
        await sender.send(
            session,
            channel="email",
            recipient_kind="coach",
            recipient_id=coach_id,
            notification_type="appointment_rescheduled",
            payload={
                "appointment_id": successor.id,
                "member_id": appointment.member_id,
                **times,
            },
        )
    out = await booking.appointment_out(session, successor)
    await session.commit()
    return out


async def request_reschedule(
    session: AsyncSession,
    principal: Principal,
    appointment: Appointment,
    *,
    on_behalf_of_coach_id: str | None,
) -> RescheduleRequestOut:
    """Coach/admin-initiated reschedule: send the member a tokened link; commits.

    The member fulfils via ``POST /v1/appointments/{id}/reschedule`` (or the tokened resolver);
    the link's TTL comes from the programme's ``reschedule_link_ttl_days``.
    """
    if appointment.status != "confirmed":
        raise HTTPException(
            status_code=409, detail=f"Appointment is {appointment.status}, not reschedulable"
        )
    await validate_window(session, appointment, action="Reschedule")

    programme = await session.get(Programme, appointment.programme_id)
    ttl = timedelta(days=programme.reschedule_link_ttl_days if programme else 7)
    token = mint_link_token(_PURPOSE, appointment.id, ttl)
    expires_at = clock.now() + ttl
    session.add(
        RescheduleToken(
            appointment_id=appointment.id,
            token_hash=hash_token(token),
            audience="member",
            expires_at=expires_at,
        )
    )
    slot = await session.get(Slot, appointment.slot_id)
    await get_sender().send(
        session,
        channel="email",
        recipient_kind="member",
        recipient_id=appointment.member_id,
        notification_type="reschedule_requested",
        payload={
            "appointment_id": appointment.id,
            "coach_id": appointment.coach_id,
            "start_utc": slot.start_utc.isoformat() if slot else None,
            "link": f"{RESCHEDULE_PAGE_LINK}?token={token}",
            "booking_page_link": BOOKING_PAGE_LINK,
            "on_behalf_of_coach_id": on_behalf_of_coach_id,
        },
    )
    await session.commit()
    return RescheduleRequestOut(appointment_id=appointment.id, expires_at=expires_at)


async def resolve_link(session: AsyncSession, token: str) -> RescheduleLinkOut:
    """Resolve a tokened reschedule link: appointment + same-coach bookable slots.

    404 for anything invalid (bad signature, wrong purpose, unknown/unmatched token); 410 when
    the link has expired, been spent, or the appointment is no longer open to rescheduling. The
    slots honour the same matching conditions as the original booking — the coach is unchanged,
    so language criteria hold by construction.
    """
    appointment_id = verify_link_token(token, _PURPOSE)  # 410 expired / 404 invalid
    row = (
        await session.execute(
            select(RescheduleToken).where(
                RescheduleToken.appointment_id == appointment_id,
                RescheduleToken.token_hash == hash_token(token),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Invalid link")
    if row.used_at is not None:
        raise HTTPException(status_code=410, detail="This link has already been used")
    appointment = await session.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Invalid link")
    if appointment.status != "confirmed":
        raise HTTPException(
            status_code=410, detail="This appointment is no longer open to rescheduling"
        )
    slots = await booking.browse_slots(
        session,
        appointment.member_id,
        from_date=clock.now().date(),
        to_date=await horizon_end(session, appointment.coach_id),
        language_match=False,  # the coach is fixed below; criteria hold by construction
        coach_id=appointment.coach_id,
    )
    return RescheduleLinkOut(
        appointment=await booking.appointment_out(session, appointment),
        slots=[s for s in slots if s.slot_id != appointment.slot_id],
    )


async def history(session: AsyncSession, appointment: Appointment) -> AppointmentHistoryOut:
    """The full reschedule chain for any record in it, oldest first."""
    root = appointment
    while root.original_appointment_id is not None:
        predecessor = await session.get(Appointment, root.original_appointment_id)
        if predecessor is None:
            break
        root = predecessor
    chain = [root]
    while True:
        successor = (
            await session.execute(
                select(Appointment).where(Appointment.original_appointment_id == chain[-1].id)
            )
        ).scalar_one_or_none()
        if successor is None:
            break
        chain.append(successor)
    events = (
        (
            await session.execute(
                select(RescheduleEvent)
                .where(RescheduleEvent.from_appointment_id.in_([a.id for a in chain]))
                .order_by(RescheduleEvent.occurred_at)
            )
        )
        .scalars()
        .all()
    )
    return AppointmentHistoryOut(
        appointments=await booking.appointments_out(session, chain),
        reschedules=[
            RescheduleEventOut(
                from_appointment_id=e.from_appointment_id,
                to_appointment_id=e.to_appointment_id,
                initiated_by=e.initiated_by,
                from_slot_id=e.from_slot_id,
                to_slot_id=e.to_slot_id,
                occurred_at=e.occurred_at,
            )
            for e in events
        ],
    )
