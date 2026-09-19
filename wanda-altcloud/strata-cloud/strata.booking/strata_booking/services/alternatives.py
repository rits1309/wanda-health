"""Suggested alternatives after a coach cancellation.

Order of preference: (1) different coaches available at the exact original time; (2) similar
slots on the same day within the programme's configurable window either side of the original
time; (3) always, the booking-page link for self-service. Alternatives respect the same
matching conditions as normal booking — when the original booking was language-matched, only
coaches speaking the member's preferred language are suggested. Each suggestion is an
accept-or-reject-only link to a pre-set booking screen; acceptance fails gracefully (409) if
another member has since taken the slot.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from strata_core.db.common import new_id
from strata_core.domains.booking import Appointment, Slot, SuggestedAlternative
from strata_core.domains.kernel import Programme, ProgrammeAssignment, UserProfile

from strata_booking.core.clock import clock
from strata_booking.core.security import ROLE_MEMBER, Principal
from strata_booking.services import booking
from strata_booking.services.notifications import get_sender
from strata_booking.services.outbox import emit
from strata_booking.services.people import MemberView, find_member
from strata_booking.services.tokens import (
    BOOKING_PAGE_LINK,
    hash_token,
    mint_link_token,
    verify_link_token,
)

MAX_SUGGESTIONS = 5
_PURPOSE = "alternative"


async def _candidate_slots(
    session: AsyncSession,
    appointment: Appointment,
    original: Slot,
    member: MemberView,
    window_hours: int,
) -> list[tuple[Slot, str]]:
    """(slot, kind) candidates in preference order, language-matched per the original booking."""
    programme_coaches = set(
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
    )
    if appointment.booking_language_matched:
        rows = (
            (
                await session.execute(
                    select(UserProfile)
                    .options(selectinload(UserProfile.user_languages))
                    .where(UserProfile.id.in_(programme_coaches))
                )
            )
            .scalars()
            .all()
        )
        preferred = appointment.booking_preferred_language or member.preferred_language
        programme_coaches = {c.id for c in rows if preferred in c.language_codes}

    window = timedelta(hours=window_hours)
    candidates = (
        (
            await session.execute(
                select(Slot)
                .where(
                    Slot.coach_id.in_(programme_coaches),
                    Slot.status == "available",
                    Slot.start_utc >= original.start_utc - window,
                    Slot.start_utc <= original.start_utc + window,
                    Slot.start_utc > clock.now(),
                )
                .order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )

    member_tz = ZoneInfo(member.timezone)
    original_day = original.start_utc.astimezone(member_tz).date()
    same_time: list[Slot] = []
    same_day: list[Slot] = []
    for slot in candidates:
        if slot.id == original.id:
            continue
        if (
            slot.start_utc == original.start_utc
            and slot.duration_minutes == original.duration_minutes
            and slot.coach_id != appointment.coach_id
        ):
            same_time.append(slot)
        elif slot.start_utc.astimezone(member_tz).date() == original_day:
            same_day.append(slot)
    same_day.sort(key=lambda s: abs((s.start_utc - original.start_utc).total_seconds()))
    ranked = [(s, "same_time_other_coach") for s in same_time]
    ranked += [(s, "same_day_window") for s in same_day]
    return ranked[:MAX_SUGGESTIONS]


async def suggest_for_cancelled(
    session: AsyncSession,
    appointment: Appointment,
    original: Slot,
    *,
    reason: str,
    acting_user_id: str,
    on_behalf_of_coach_id: str | None,
) -> None:
    """Create the suggestion set + email; called inside the cancelling transaction."""
    member = await find_member(session, appointment.member_id)
    programme = await session.get(Programme, appointment.programme_id)
    window_hours = programme.suggestion_window_hours if programme else 4
    ttl = timedelta(days=programme.reschedule_link_ttl_days if programme else 7)

    links: list[dict[str, object]] = []
    if member is not None:
        for rank, (slot, kind) in enumerate(
            await _candidate_slots(session, appointment, original, member, window_hours), start=1
        ):
            alternative_id = new_id()
            token = mint_link_token(_PURPOSE, alternative_id, ttl)
            session.add(
                SuggestedAlternative(
                    id=alternative_id,
                    cancelled_appointment_id=appointment.id,
                    rank=rank,
                    kind=kind,
                    slot_id=slot.id,
                    token_hash=hash_token(token),
                )
            )
            links.append(
                {
                    "rank": rank,
                    "kind": kind,
                    "slot_id": slot.id,
                    "coach_id": slot.coach_id,
                    "start_utc": slot.start_utc.isoformat(),
                    "duration_minutes": slot.duration_minutes,
                    "link": f"/alternatives?token={token}",
                }
            )

    await get_sender().send(
        session,
        channel="email",
        recipient_kind="member",
        recipient_id=appointment.member_id,
        notification_type="appointment_alternatives_suggested",
        payload={
            "appointment_id": appointment.id,
            "coach_id": appointment.coach_id,
            "original_start_utc": original.start_utc.isoformat(),
            "reason": reason,
            "alternatives": links,  # may be empty — the booking link below always works
            "booking_page_link": BOOKING_PAGE_LINK,
        },
    )
    emit(
        session,
        "AppointmentAlternativesSuggested",
        {
            "appointment_id": appointment.id,
            "member_id": appointment.member_id,
            "coach_id": appointment.coach_id,
            "alternatives": [
                {"rank": a["rank"], "kind": a["kind"], "slot_id": a["slot_id"]} for a in links
            ],
            "acting_user_id": acting_user_id,
            "on_behalf_of_coach_id": on_behalf_of_coach_id,
        },
    )


async def _resolve(session: AsyncSession, token: str) -> SuggestedAlternative:
    alternative_id = verify_link_token(token, _PURPOSE)
    alternative = await session.get(SuggestedAlternative, alternative_id)
    if alternative is None or alternative.token_hash != hash_token(token):
        raise HTTPException(status_code=404, detail="Invalid link")
    return alternative


async def describe(session: AsyncSession, token: str) -> dict[str, object]:
    """The pre-set booking screen payload: all details fixed, accept or reject only."""
    alternative = await _resolve(session, token)
    slot = await session.get(Slot, alternative.slot_id)
    appointment = await session.get(Appointment, alternative.cancelled_appointment_id)
    if slot is None or appointment is None:
        raise HTTPException(status_code=404, detail="Invalid link")
    coach = await session.get(UserProfile, slot.coach_id)
    return {
        "status": alternative.status,
        "slot_available": slot.status == "available",
        "coach_id": slot.coach_id,
        "coach_name": coach.effective_display_name if coach else slot.coach_id,
        "start_utc": slot.start_utc,
        "end_utc": slot.end_utc,
        "duration_minutes": slot.duration_minutes,
        "booking_page_link": BOOKING_PAGE_LINK,
    }


async def accept(session: AsyncSession, token: str) -> str:
    """Accept a suggested alternative: books it for the member; returns appointment id.

    Fails gracefully with 409 (and marks the suggestion ``slot_taken``) when another member got
    the slot first — the member is pointed back at the booking page.
    """
    alternative = await _resolve(session, token)
    if alternative.status == "accepted":
        raise HTTPException(status_code=409, detail="Already accepted")
    if alternative.status in ("rejected", "expired", "slot_taken"):
        raise HTTPException(status_code=410, detail="This suggestion is no longer open")
    appointment = await session.get(Appointment, alternative.cancelled_appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Invalid link")

    slot = await session.get(Slot, alternative.slot_id)
    if slot is None or slot.status != "available":
        alternative.status = "slot_taken"
        await session.commit()
        raise HTTPException(
            status_code=409,
            detail="That slot has since been taken — please choose another time on the "
            "booking page",
        )

    # A synthetic member principal: the tokened link itself is the authentication
    # (verified by _resolve above); booking only needs the member's sub + kind. The
    # empty `token` is the Principal's raw-bearer field, not a credential (B106).
    principal = Principal(sub=appointment.member_id, roles=[ROLE_MEMBER], token="")  # nosec B106
    out, _ = await booking.book(
        session,
        principal,
        slot_id=alternative.slot_id,
        member_id=appointment.member_id,
        language_matched=appointment.booking_language_matched,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    alternative.status = "accepted"
    await session.commit()
    return out.id


async def reject(session: AsyncSession, token: str) -> None:
    """Reject: nothing is booked; the member may still use the booking page."""
    alternative = await _resolve(session, token)
    if alternative.status == "offered":
        alternative.status = "rejected"
        await session.commit()
