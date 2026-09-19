"""Availability pattern management: CRUD, versioning, regeneration, impact.

Every mutation snapshots the pattern into ``availability_pattern_versions`` (audit), emits the
business events to the transactional outbox in the same transaction, and regenerates future
slots. A booked slot the new shape no longer honours is cancelled and the member notified —
Phase 1 up to sends a plain cancellation email with the booking-page link; the
suggested-alternatives engine upgrades ``cancel_booked_slot`` in place.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import (
    Appointment,
    AvailabilityPattern,
    AvailabilityPatternVersion,
    Slot,
)

from strata_booking.core.clock import clock
from strata_booking.schemas.availability import ImpactedAppointment, PatternBody
from strata_booking.services import slot_generation
from strata_booking.services.authz import ActingContext
from strata_booking.services.outbox import emit
from strata_booking.services.people import display_names_for

_log = structlog.get_logger("availability")


def _pattern_payload(pattern: AvailabilityPattern, actor: ActingContext) -> dict[str, object]:
    return {
        "pattern_id": pattern.id,
        "coach_id": pattern.coach_id,
        "version": pattern.version,
        "days_of_week": pattern.days_of_week,
        "start_time_local": pattern.start_time_local.isoformat(),
        "end_time_local": pattern.end_time_local.isoformat(),
        "slot_duration_minutes": pattern.slot_duration_minutes,
        "timezone": pattern.timezone,
        "active_from": pattern.active_from.isoformat(),
        "active_to": pattern.active_to.isoformat(),
        "acting_user_id": actor.acting_user_id,
        "on_behalf_of_coach_id": actor.on_behalf_of_coach_id,
    }


def _snapshot(session: AsyncSession, pattern: AvailabilityPattern, actor: ActingContext) -> None:
    session.add(
        AvailabilityPatternVersion(
            pattern_id=pattern.id,
            version=pattern.version,
            snapshot={
                k: v
                for k, v in _pattern_payload(pattern, actor).items()
                if k not in ("acting_user_id", "on_behalf_of_coach_id")
            },
            changed_by=actor.acting_user_id,
            on_behalf_of_coach_id=actor.on_behalf_of_coach_id,
        )
    )


async def get_owned_pattern(
    session: AsyncSession, pattern_id: str, coach_id: str
) -> AvailabilityPattern:
    pattern = (
        await session.execute(
            select(AvailabilityPattern).where(AvailabilityPattern.id == pattern_id)
        )
    ).scalar_one_or_none()
    if pattern is None or pattern.status == "deleted":
        raise HTTPException(status_code=404, detail="Availability pattern not found")
    if pattern.coach_id != coach_id:
        raise HTTPException(status_code=403, detail="Pattern belongs to another coach")
    return pattern


async def list_patterns(session: AsyncSession, coach_id: str) -> list[AvailabilityPattern]:
    return list(
        (
            await session.execute(
                select(AvailabilityPattern)
                .where(
                    AvailabilityPattern.coach_id == coach_id,
                    AvailabilityPattern.status == "active",
                )
                .order_by(AvailabilityPattern.created_at)
            )
        )
        .scalars()
        .all()
    )


async def create_pattern(
    session: AsyncSession, actor: ActingContext, body: PatternBody
) -> AvailabilityPattern:
    pattern = AvailabilityPattern(coach_id=actor.coach_id, **body.model_dump())
    session.add(pattern)
    await session.flush()
    _snapshot(session, pattern, actor)
    created = await slot_generation.generate_slots_for_pattern(
        session,
        pattern,
        from_date=clock.now().date(),
        to_date=await slot_generation.horizon_end(session, actor.coach_id),
    )
    emit(session, "AvailabilityPatternCreated", _pattern_payload(pattern, actor))
    emit(
        session,
        "SlotsGenerated",
        {
            "pattern_id": pattern.id,
            "coach_id": pattern.coach_id,
            "count": created,
            "acting_user_id": actor.acting_user_id,
        },
    )
    await session.commit()
    return pattern


async def _future_slots(session: AsyncSession, pattern_id: str, now: datetime) -> list[Slot]:
    return list(
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern_id, Slot.start_utc > now)
            )
        )
        .scalars()
        .all()
    )


async def _booked_appointment(session: AsyncSession, slot_id: str) -> Appointment | None:
    return (
        await session.execute(
            select(Appointment).where(
                Appointment.slot_id == slot_id, Appointment.status == "confirmed"
            )
        )
    ).scalar_one_or_none()


def observe_slot_desync(
    session: AsyncSession,
    actor: ActingContext,
    slot: Slot,
    *,
    healed_to: str,
    touchpoint: str,
) -> None:
    """Record a lifecycle desync — a booked-status slot with no confirmed appointment.

    Normal use cannot produce this shape (every booked/confirmed pair is written in one
    transaction; 's partial unique index is the authoritative guard), so seeing it means
    out-of-band mutation. The caller still heals the slot; this makes the heal observable — a
    structured warning plus a ``SlotDesyncObserved`` outbox event, identifiers only — instead
    of the silent conversion the review caught. Decision: heal-and-observe, 03-08-2026.
    """
    _log.warning(
        "slot_desync_observed",
        slot_id=slot.id,
        coach_id=slot.coach_id,
        healed_to=healed_to,
        touchpoint=touchpoint,
    )
    emit(
        session,
        "SlotDesyncObserved",
        {
            "slot_id": slot.id,
            "coach_id": slot.coach_id,
            "healed_to": healed_to,
            "touchpoint": touchpoint,
            "acting_user_id": actor.acting_user_id,
            "on_behalf_of_coach_id": actor.on_behalf_of_coach_id,
        },
    )


async def cancel_booked_slot(
    session: AsyncSession,
    actor: ActingContext,
    slot: Slot,
    appointment: Appointment,
    *,
    reason: str,
) -> None:
    """Cancel a booked appointment because its slot can no longer be honoured.

    The member receives the suggested-alternatives email (same-time other coaches →
    same-day window → booking-page link) via the alternatives engine.
    """
    appointment.status = "cancelled"
    appointment.cancelled_by = "coach"
    appointment.cancellation_reason = reason
    slot.status = "cancelled"
    appointment.idempotency_key = None  # release the member:slot key on cancel
    emit(
        session,
        "AppointmentCancelled",
        {
            "appointment_id": appointment.id,
            "member_id": appointment.member_id,
            "coach_id": appointment.coach_id,
            "cancelled_by": "coach",
            "reason": reason,
            "acting_user_id": actor.acting_user_id,
            "on_behalf_of_coach_id": actor.on_behalf_of_coach_id,
        },
    )
    # The member is emailed suggested alternatives — same-time other coaches,
    # then same-day slots in the programme window, plus the booking-page link.
    from strata_booking.services.alternatives import suggest_for_cancelled

    await suggest_for_cancelled(
        session,
        appointment,
        slot,
        reason=reason,
        acting_user_id=actor.acting_user_id,
        on_behalf_of_coach_id=actor.on_behalf_of_coach_id,
    )


async def _regenerate(
    session: AsyncSession,
    actor: ActingContext,
    pattern: AvailabilityPattern,
    *,
    reason: str,
) -> int:
    """Rebuild the pattern's future slots; cancel booked ones the new shape drops.

    Unbooked future slots are deleted and re-created from the new version; a booked slot whose
    exact window still exists under the new shape is kept (and skipped by idempotent
    generation), otherwise it is cancelled via :func:`cancel_booked_slot`. A booked-status
    slot with no confirmed appointment is retired like any unbooked slot, then observed via
    :func:`observe_slot_desync` after regeneration, reporting the state that actually
    commits — the revive upsert may have brought it straight back.
    """
    now = clock.now()
    horizon = await slot_generation.horizon_end(session, pattern.coach_id)
    keep: set[tuple[datetime, datetime]] = (
        set(slot_generation.occurrences(pattern, now.date(), horizon))
        if pattern.status == "active"
        else set()
    )

    retire_ids: list[str] = []
    desynced: list[Slot] = []
    for slot in await _future_slots(session, pattern.id, now):
        appointment = await _booked_appointment(session, slot.id)
        if appointment is not None:
            if (slot.start_utc.astimezone(UTC), slot.end_utc.astimezone(UTC)) not in keep:
                await cancel_booked_slot(session, actor, slot, appointment, reason=reason)
            continue
        if slot.status == "booked":
            # The slot lies (no confirmed appointment behind it): retiring it is the right
            # heal, but the desync itself must be recorded. Observation is
            # deferred until after regeneration below — the revive upsert may immediately
            # bring the slot back, and the event must report the state that commits, not
            # the intermediate retire.
            desynced.append(slot)
        retire_ids.append(slot.id)
    if retire_ids:
        # Retire, never delete: appointments, reschedule events and suggested
        # alternatives reference slots — the slot row carries the "when" of that history.
        # A re-covered time is revived by idempotent generation below. Core-side like the
        # revive upsert, so both act on database truth rather than in-session object state.
        await session.execute(
            update(Slot).where(Slot.id.in_(retire_ids)).values(status="cancelled")
        )

    created = 0
    if pattern.status == "active":
        created = await slot_generation.generate_slots_for_pattern(
            session, pattern, from_date=now.date(), to_date=horizon
        )
    for slot in desynced:
        # Both the retire and the revive ran core-side, so the in-session row is stale:
        # refresh to the status that will actually commit before reporting it.
        await session.refresh(slot)
        observe_slot_desync(
            session, actor, slot, healed_to=slot.status, touchpoint="availability_regenerate"
        )
    return created


async def update_pattern(
    session: AsyncSession, actor: ActingContext, pattern: AvailabilityPattern, body: PatternBody
) -> AvailabilityPattern:
    for field, value in body.model_dump().items():
        setattr(pattern, field, value)
    pattern.version += 1
    _snapshot(session, pattern, actor)
    created = await _regenerate(session, actor, pattern, reason="availability_reshaped")
    emit(session, "AvailabilityPatternUpdated", _pattern_payload(pattern, actor))
    emit(
        session,
        "SlotsGenerated",
        {
            "pattern_id": pattern.id,
            "coach_id": pattern.coach_id,
            "count": created,
            "acting_user_id": actor.acting_user_id,
        },
    )
    await session.commit()
    return pattern


async def delete_pattern(
    session: AsyncSession, actor: ActingContext, pattern: AvailabilityPattern
) -> None:
    pattern.status = "deleted"
    pattern.version += 1
    _snapshot(session, pattern, actor)
    await _regenerate(session, actor, pattern, reason="availability_pattern_deleted")
    payload = _pattern_payload(pattern, actor)
    payload["deleted"] = True
    emit(session, "AvailabilityPatternUpdated", payload)
    await session.commit()


async def pattern_impact(
    session: AsyncSession,
    pattern: AvailabilityPattern,
    proposed: PatternBody | None,
) -> list[ImpactedAppointment]:
    """Dry-run: which booked appointments would cancel under a proposed pattern change.

    ``proposed=None`` means deletion — every future booked appointment is impacted. Read-only.
    """
    now = clock.now()
    keep: set[tuple[datetime, datetime]] = set()
    if proposed is not None:
        probe = AvailabilityPattern(coach_id=pattern.coach_id, **proposed.model_dump())
        horizon = await slot_generation.horizon_end(session, pattern.coach_id)
        keep = set(slot_generation.occurrences(probe, now.date(), horizon))

    hits: list[tuple[Appointment, Slot]] = []
    for slot in await _future_slots(session, pattern.id, now):
        appointment = await _booked_appointment(session, slot.id)
        if appointment is None:
            continue
        if (slot.start_utc.astimezone(UTC), slot.end_utc.astimezone(UTC)) not in keep:
            hits.append((appointment, slot))
    # Member names ride the preview — one batched lookup per response.
    names = await display_names_for(session, {appointment.member_id for appointment, _ in hits})
    return [
        ImpactedAppointment(
            appointment_id=appointment.id,
            member_id=appointment.member_id,
            member_name=names.get(appointment.member_id, appointment.member_id),
            slot_start_utc=slot.start_utc,
            slot_end_utc=slot.end_utc,
        )
        for appointment, slot in hits
    ]
