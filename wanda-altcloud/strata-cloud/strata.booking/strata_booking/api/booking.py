"""Slot browsing and appointment endpoints."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from strata_booking.core.security import (
    ROLE_ADMIN,
    ROLE_COACH,
    ROLE_MEMBER,
    Principal,
    current_principal,
    require_role,
)
from strata_booking.db.session import get_session
from strata_booking.schemas.booking import (
    AppointmentHistoryOut,
    AppointmentOut,
    BookingBody,
    BrowseSlot,
    ReassignBody,
    ReassignmentOption,
    RescheduleBody,
    RescheduleRequestOut,
)
from strata_booking.schemas.policy import CancelBody
from strata_booking.services import booking, cancellation, no_show, reassignment, rescheduling
from strata_booking.services.authz import resolve_coach_actor

_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Malformed request body"},
    401: {"description": "Not authenticated"},
    403: {"description": "Not permitted"},
    404: {"description": "Not found"},
    409: {"description": "Slot no longer available"},
    422: {"description": "Validation error"},
}

slots_router = APIRouter(prefix="/slots", tags=["booking"], responses=_RESPONSES)
appointments_router = APIRouter(prefix="/appointments", tags=["booking"], responses=_RESPONSES)

Session = Annotated[AsyncSession, Depends(get_session)]
MemberOnly = Annotated[Principal, Depends(require_role(ROLE_MEMBER))]
CoachOrAdmin = Annotated[Principal, Depends(require_role(ROLE_COACH, ROLE_ADMIN))]
AnyRole = Annotated[Principal, Depends(current_principal)]


def _on_behalf(principal: Principal, appointment_coach_id: str) -> str | None:
    """Act-for-coach attribution: set only when an admin acts on another coach's appointment.

    Capability-based (never a role collapse): the appointment's own coach —
    even one who also holds Admin — is self-serving, so no attribution.
    """
    if principal.has_role(ROLE_COACH) and appointment_coach_id == principal.sub:
        return None
    return appointment_coach_id if principal.has_role(ROLE_ADMIN) else None


@slots_router.get("")
async def browse_slots(
    principal: MemberOnly,
    session: Session,
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    language_match: Annotated[bool, Query()] = False,
    coach_id: Annotated[str | None, Query()] = None,
) -> list[BrowseSlot]:
    """Available slots for the member's programmes; booked/unavailable are hidden."""
    if to_date < from_date:
        raise HTTPException(status_code=400, detail="'to' must not be before 'from'")
    return await booking.browse_slots(
        session,
        principal.sub,
        from_date=from_date,
        to_date=to_date,
        language_match=language_match,
        coach_id=coach_id,
    )


@appointments_router.post("", status_code=201)
async def create_appointment(
    body: BookingBody,
    principal: AnyRole,
    session: Session,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    coach_id: Annotated[str | None, Query()] = None,
) -> AppointmentOut:
    """Book a slot. Members book for themselves; coaches/admins book on behalf of a member.

    Idempotent on ``member_id:slot_id`` — a retried request returns the same appointment with
    status 200 instead of 201.
    """
    # Capability dispatch: a member books themselves; coaches/admins book on behalf.
    # A multi-role caller books themselves the member way and others the coach way.
    if principal.has_role(ROLE_MEMBER) and body.member_id in (None, principal.sub):
        member_id = principal.sub
        created_by = "member"
        on_behalf = None
        restrict_to = None
    elif principal.has_role(ROLE_COACH, ROLE_ADMIN):
        if body.member_id is None:
            raise HTTPException(
                status_code=422, detail="member_id is required when booking on behalf of a member"
            )
        actor = await resolve_coach_actor(session, principal, coach_id)
        member_id = body.member_id
        # created_by is recorded — admin when acting for a coach, else coach.
        created_by = "admin" if actor.on_behalf_of_coach_id else "coach"
        on_behalf = actor.on_behalf_of_coach_id
        restrict_to = actor.coach_id  # on-behalf bookings target the acting coach's own slots
    elif principal.has_role(ROLE_MEMBER):
        raise HTTPException(status_code=403, detail="Members book only for themselves")
    else:
        raise HTTPException(status_code=403, detail="Insufficient role")

    expected_key = f"{member_id}:{body.slot_id}"
    if idempotency_key is not None and idempotency_key != expected_key:
        raise HTTPException(
            status_code=422,
            detail=f"Idempotency-Key must be '{expected_key}' (member_id:slot_id)",
        )

    out, replayed = await booking.book(
        session,
        principal,
        slot_id=body.slot_id,
        member_id=member_id,
        language_matched=body.language_matched,
        created_by=created_by,
        on_behalf_of_coach_id=on_behalf,
        restrict_to_coach_id=restrict_to,
    )
    if replayed:
        response.status_code = 200
    return out


@appointments_router.post("/{appointment_id}/cancel", status_code=204)
async def cancel_appointment(
    appointment_id: str, body: CancelBody, principal: AnyRole, session: Session
) -> None:
    """Cancel per the effective policy: 403 not-allowed-by, 409 window passed."""
    appointment = await booking.get_appointment_scoped(session, principal, appointment_id)
    on_behalf = _on_behalf(principal, appointment.coach_id)
    await cancellation.cancel_appointment(
        session, principal, appointment, reason=body.reason, on_behalf_of_coach_id=on_behalf
    )


@appointments_router.post("/{appointment_id}/reschedule", status_code=201)
async def reschedule_appointment(
    appointment_id: str, body: RescheduleBody, principal: MemberOnly, session: Session
) -> AppointmentOut:
    """Member-fulfilled reschedule: move to a new slot; returns the successor."""
    appointment = await booking.get_appointment_scoped(session, principal, appointment_id)
    return await rescheduling.reschedule_appointment(
        session, principal, appointment, new_slot_id=body.slot_id
    )


@appointments_router.post("/{appointment_id}/reschedule-request")
async def request_reschedule(
    appointment_id: str, principal: CoachOrAdmin, session: Session
) -> RescheduleRequestOut:
    """Coach/admin-initiated: sends the member a tokened reschedule link."""
    appointment = await booking.get_appointment_scoped(session, principal, appointment_id)
    on_behalf = _on_behalf(principal, appointment.coach_id)
    return await rescheduling.request_reschedule(
        session, principal, appointment, on_behalf_of_coach_id=on_behalf
    )


@appointments_router.get("/{appointment_id}/reassignment-options")
async def reassignment_options(
    appointment_id: str, principal: CoachOrAdmin, session: Session
) -> list[ReassignmentOption]:
    """Other coaches' same-time slots per the original booking criteria; empty ⇒ unavailable."""
    appointment = await booking.get_appointment_scoped(session, principal, appointment_id)
    return await reassignment.reassignment_options(session, appointment)


@appointments_router.post("/{appointment_id}/reassign", status_code=204)
async def reassign_appointment(
    appointment_id: str, body: ReassignBody, principal: CoachOrAdmin, session: Session
) -> None:
    """Hand the appointment to another coach at the exact same time."""
    appointment = await booking.get_appointment_scoped(session, principal, appointment_id)
    on_behalf = _on_behalf(principal, appointment.coach_id)
    await reassignment.reassign(
        session, principal, appointment, new_slot_id=body.slot_id, on_behalf_of_coach_id=on_behalf
    )


@appointments_router.post("/{appointment_id}/no-show", status_code=204)
async def mark_no_show(appointment_id: str, principal: CoachOrAdmin, session: Session) -> None:
    """Manually mark a member as a no-show — after the appointment start."""
    appointment = await booking.get_appointment_scoped(session, principal, appointment_id)
    on_behalf = _on_behalf(principal, appointment.coach_id)
    await no_show.mark_no_show(session, principal, appointment, on_behalf_of_coach_id=on_behalf)


@appointments_router.get("/{appointment_id}/history")
async def appointment_history(
    appointment_id: str, principal: AnyRole, session: Session
) -> AppointmentHistoryOut:
    """Full reschedule chain, oldest record first."""
    appointment = await booking.get_appointment_scoped(session, principal, appointment_id)
    return await rescheduling.history(session, appointment)


@appointments_router.get("")
async def list_appointments(principal: AnyRole, session: Session) -> list[AppointmentOut]:
    rows = await booking.list_appointments_scoped(session, principal)
    return await booking.appointments_out(session, rows)


@appointments_router.get("/{appointment_id}")
async def get_appointment(
    appointment_id: str, principal: AnyRole, session: Session
) -> AppointmentOut:
    appointment = await booking.get_appointment_scoped(session, principal, appointment_id)
    return await booking.appointment_out(session, appointment)
