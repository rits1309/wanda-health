"""Booking: member slot browse, booking with idempotency, scoped reads.

- **Browse** shows only future ``available`` slots of coaches in the member's programmes; the
  language toggle ON restricts to coaches speaking the member's preferred language, OFF shows
  all programme coaches. Browsing defensively tops up lazy generation inside the horizon.
- **Booking** is idempotent on the key ``member_id:slot_id`` — a retry returns the same
  appointment. One member per slot is enforced with a row lock on the slot. Booking snapshots
  the language-match criteria, notifies both parties, schedules reminders, and emits
  ``AppointmentBooked`` in the same transaction.
"""

from __future__ import annotations

from datetime import date, time, timedelta

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from strata_core.domains.booking import Appointment, AvailabilityPattern, Reminder, Slot
from strata_core.domains.kernel import Programme, ProgrammeAssignment, UserProfile

from strata_booking.core.clock import clock
from strata_booking.core.security import ROLE_ADMIN, ROLE_COACH, ROLE_MEMBER, Principal
from strata_booking.schemas.booking import AppointmentOut, BrowseSlot
from strata_booking.services import people, slot_generation
from strata_booking.services.authz import administered_programme_ids, programme_ids_for
from strata_booking.services.notifications import get_sender
from strata_booking.services.outbox import emit
from strata_booking.services.people import get_member


async def ensure_generated(session: AsyncSession, coach_ids: list[str], to_date: date) -> None:
    """Defensive lazy top-up: materialise any pattern lagging behind the query window."""
    if not coach_ids:
        return
    patterns = (
        (
            await session.execute(
                select(AvailabilityPattern).where(
                    AvailabilityPattern.coach_id.in_(coach_ids),
                    AvailabilityPattern.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    today = clock.now().date()
    for pattern in patterns:
        cap = min(to_date, await slot_generation.horizon_end(session, pattern.coach_id))
        if pattern.generation_watermark is None or pattern.generation_watermark < cap:
            start = (
                today
                if pattern.generation_watermark is None
                else max(today, pattern.generation_watermark)
            )
            await slot_generation.generate_slots_for_pattern(
                session, pattern, from_date=start, to_date=cap
            )
    await session.commit()  # top-up must persist even though browsing is a read


async def browse_slots(
    session: AsyncSession,
    member_id: str,
    *,
    from_date: date,
    to_date: date,
    language_match: bool,
    coach_id: str | None = None,
) -> list[BrowseSlot]:
    member = await get_member(session, member_id)
    programme_ids = await programme_ids_for(session, "member", member_id)
    if not programme_ids:
        return []

    coach_rows = (
        (
            await session.execute(
                select(UserProfile)
                .options(selectinload(UserProfile.user_languages))
                .join(
                    ProgrammeAssignment,
                    (ProgrammeAssignment.user_id == UserProfile.id)
                    & (ProgrammeAssignment.assigned_as("Coach")),
                )
                .where(ProgrammeAssignment.programme_id.in_(programme_ids))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    if language_match:
        coach_rows = [c for c in coach_rows if member.preferred_language in c.language_codes]
    if coach_id is not None:
        coach_rows = [c for c in coach_rows if c.id == coach_id]
    coaches = {c.id: c for c in coach_rows}
    if not coaches:
        return []

    await ensure_generated(session, list(coaches), to_date)

    now = clock.now()
    slots = (
        (
            await session.execute(
                select(Slot)
                .where(
                    Slot.coach_id.in_(list(coaches)),
                    Slot.status == "available",
                    Slot.start_utc > now,
                    Slot.start_utc >= slot_generation.to_utc(from_date, time.min, "UTC"),
                    Slot.start_utc <= slot_generation.to_utc(to_date, time.max, "UTC"),
                )
                .order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    return [
        BrowseSlot(
            slot_id=s.id,
            coach_id=s.coach_id,
            coach_name=coaches[s.coach_id].effective_display_name,
            coach_languages=coaches[s.coach_id].language_codes,
            start_utc=s.start_utc,
            end_utc=s.end_utc,
            duration_minutes=s.duration_minutes,
        )
        for s in slots
    ]


async def _appointment_out(
    session: AsyncSession, appointment: Appointment, *, member_name: str | None = None
) -> AppointmentOut:
    slot = await session.get(Slot, appointment.slot_id)
    if slot is None:  # FK guarantees this; guard instead of assert (stripped under -O)
        raise HTTPException(status_code=500, detail="Appointment slot missing")
    if member_name is None:
        names = await people.display_names_for(session, {appointment.member_id})
        member_name = names.get(appointment.member_id, appointment.member_id)
    return AppointmentOut(
        id=appointment.id,
        slot_id=appointment.slot_id,
        coach_id=appointment.coach_id,
        member_id=appointment.member_id,
        member_name=member_name,
        programme_id=appointment.programme_id,
        status=appointment.status,
        created_by=appointment.created_by,
        start_utc=slot.start_utc,
        end_utc=slot.end_utc,
        booking_language_matched=appointment.booking_language_matched,
        original_appointment_id=appointment.original_appointment_id,
    )


async def schedule_reminders(session: AsyncSession, appointment: Appointment, slot: Slot) -> None:
    """Create pending reminder rows for both parties at the programme's lead time."""
    programme = await session.get(Programme, appointment.programme_id)
    lead = timedelta(hours=programme.reminder_lead_hours if programme else 24)
    due = slot.start_utc - lead
    if due <= clock.now():
        return
    session.add(
        Reminder(
            appointment_id=appointment.id,
            recipient_kind="member",
            recipient_id=appointment.member_id,
            due_at_utc=due,
        )
    )
    session.add(
        Reminder(
            appointment_id=appointment.id,
            recipient_kind="coach",
            recipient_id=appointment.coach_id,
            due_at_utc=due,
        )
    )


_SLOT_TAKEN_CONSTRAINT = "uq_appointments_slot_confirmed"


async def flush_appointment_insert(session: AsyncSession) -> None:
    """Flush an appointment insert, degrading a slot-uniqueness violation to 409.

    The partial unique index (one confirmed appointment per slot) is the authoritative
    guard; ``slot.status`` is a denormalized proxy a desynced row can lie through. When
    the index refuses the insert, roll back and answer with the standard conflict instead
    of letting the raw ``IntegrityError`` escape as a 500.
    """
    try:
        await session.flush()
    except IntegrityError as exc:
        if _SLOT_TAKEN_CONSTRAINT not in str(exc.orig):
            raise
        await session.rollback()
        raise HTTPException(status_code=409, detail="Slot is no longer available") from exc


async def book(
    session: AsyncSession,
    principal: Principal,
    *,
    slot_id: str,
    member_id: str,
    language_matched: bool,
    created_by: str,
    on_behalf_of_coach_id: str | None,
    restrict_to_coach_id: str | None = None,
) -> tuple[AppointmentOut, bool]:
    """Book a slot for a member; returns (appointment, replayed).

    Idempotent on ``member_id:slot_id``: a retry returns the existing appointment
    with ``replayed=True`` and creates nothing.
    """
    member = await get_member(session, member_id)
    key = f"{member_id}:{slot_id}"

    existing = (
        await session.execute(select(Appointment).where(Appointment.idempotency_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        return await _appointment_out(session, existing), True

    # Row-lock the slot: one member per slot even under concurrent booking attempts.
    slot = (
        await session.execute(select(Slot).where(Slot.id == slot_id).with_for_update())
    ).scalar_one_or_none()
    if slot is None:
        raise HTTPException(status_code=404, detail="Slot not found")
    if restrict_to_coach_id is not None and slot.coach_id != restrict_to_coach_id:
        raise HTTPException(
            status_code=403, detail="On-behalf bookings are limited to the acting coach's slots"
        )
    if slot.status != "available":
        raise HTTPException(status_code=409, detail="Slot is no longer available")
    if slot.start_utc <= clock.now():
        raise HTTPException(status_code=409, detail="Slot is in the past")

    shared = sorted(
        await programme_ids_for(session, "member", member_id)
        & await programme_ids_for(session, "coach", slot.coach_id)
    )
    if not shared:
        raise HTTPException(
            status_code=403, detail="Coach is not available to the member's programme"
        )

    slot.status = "booked"
    appointment = Appointment(
        slot_id=slot.id,
        coach_id=slot.coach_id,
        member_id=member_id,
        programme_id=shared[0],
        created_by=created_by,
        acting_user_id=principal.sub,
        on_behalf_of_coach_id=on_behalf_of_coach_id,
        booking_language_matched=language_matched,
        booking_preferred_language=member.preferred_language if language_matched else None,
        idempotency_key=key,
    )
    session.add(appointment)
    await flush_appointment_insert(session)
    await schedule_reminders(session, appointment, slot)

    emit(
        session,
        "AppointmentBooked",
        {
            "appointment_id": appointment.id,
            "slot_id": slot.id,
            "coach_id": slot.coach_id,
            "member_id": member_id,
            "programme_id": appointment.programme_id,
            "created_by": created_by,
            "language_matched": language_matched,
            "acting_user_id": principal.sub,
            "on_behalf_of_coach_id": on_behalf_of_coach_id,
        },
    )
    sender = get_sender()
    for channel in ("email", "push"):
        await sender.send(
            session,
            channel=channel,
            recipient_kind="member",
            recipient_id=member_id,
            notification_type="booking_confirmed",
            payload={"appointment_id": appointment.id, "start_utc": slot.start_utc.isoformat()},
        )
    await sender.send(
        session,
        channel="email",
        recipient_kind="coach",
        recipient_id=slot.coach_id,
        notification_type="booking_received",
        payload={"appointment_id": appointment.id, "start_utc": slot.start_utc.isoformat()},
    )
    out = await _appointment_out(session, appointment)
    await session.commit()
    return out, False


async def get_appointment_scoped(
    session: AsyncSession, principal: Principal, appointment_id: str
) -> Appointment:
    """The appointment, if any role the caller holds grants it (capability union)."""
    appointment = await session.get(Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if principal.has_role(ROLE_MEMBER) and appointment.member_id == principal.sub:
        return appointment
    if principal.has_role(ROLE_COACH) and appointment.coach_id == principal.sub:
        return appointment
    if principal.has_role(ROLE_ADMIN) and appointment.programme_id in (
        await administered_programme_ids(session, principal)
    ):
        return appointment
    raise HTTPException(status_code=403, detail="Not your appointment")


async def list_appointments_scoped(
    session: AsyncSession, principal: Principal
) -> list[Appointment]:
    """Every appointment any role the caller holds can see (capability union)."""
    scopes = []
    if principal.has_role(ROLE_MEMBER):
        scopes.append(Appointment.member_id == principal.sub)
    if principal.has_role(ROLE_COACH):
        scopes.append(Appointment.coach_id == principal.sub)
    if principal.has_role(ROLE_ADMIN):
        administered = await administered_programme_ids(session, principal)
        scopes.append(Appointment.programme_id.in_(sorted(administered)))
    if not scopes:
        raise HTTPException(status_code=403, detail="Insufficient role")
    stmt = select(Appointment).where(or_(*scopes)).order_by(Appointment.created_at)
    return list((await session.execute(stmt)).scalars().all())


async def appointment_out(session: AsyncSession, appointment: Appointment) -> AppointmentOut:
    return await _appointment_out(session, appointment)


async def appointments_out(
    session: AsyncSession, appointments: list[Appointment]
) -> list[AppointmentOut]:
    """The wire models for a list, member names batch-resolved in one query."""
    names = await people.display_names_for(session, {a.member_id for a in appointments})
    return [
        await _appointment_out(session, a, member_name=names.get(a.member_id, a.member_id))
        for a in appointments
    ]
