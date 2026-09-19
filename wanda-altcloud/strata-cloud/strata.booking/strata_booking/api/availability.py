"""Availability pattern endpoints — coach self-service, admin act-for-coach."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import AvailabilityPattern

from strata_booking.core.security import ROLE_ADMIN, ROLE_COACH, Principal, require_role
from strata_booking.db.session import get_session
from strata_booking.schemas.availability import PatternBody, PatternImpact, PatternOut
from strata_booking.services import availability
from strata_booking.services.authz import resolve_coach_actor

router = APIRouter(
    prefix="/availability-patterns",
    tags=["availability"],
    responses={
        400: {"description": "Malformed request body"},
        401: {"description": "Not authenticated"},
        403: {"description": "Not permitted"},
        404: {"description": "Not found"},
    },
)

CoachOrAdmin = Annotated[Principal, Depends(require_role(ROLE_COACH, ROLE_ADMIN))]
Session = Annotated[AsyncSession, Depends(get_session)]
CoachIdParam = Annotated[
    str | None, Query(description="Target coach (admins only; coaches act for themselves)")
]


def _out(p: AvailabilityPattern) -> PatternOut:
    return PatternOut(
        id=p.id,
        coach_id=p.coach_id,
        days_of_week=p.days_of_week,
        start_time_local=p.start_time_local.isoformat(),
        end_time_local=p.end_time_local.isoformat(),
        slot_duration_minutes=p.slot_duration_minutes,
        timezone=p.timezone,
        active_from=p.active_from,
        active_to=p.active_to,
        version=p.version,
        status=p.status,
    )


@router.post("", status_code=201)
async def create_pattern(
    body: PatternBody, principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> PatternOut:
    actor = await resolve_coach_actor(session, principal, coach_id)
    return _out(await availability.create_pattern(session, actor, body))


@router.get("")
async def list_patterns(
    principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> list[PatternOut]:
    actor = await resolve_coach_actor(session, principal, coach_id)
    return [_out(p) for p in await availability.list_patterns(session, actor.coach_id)]


@router.get("/{pattern_id}")
async def get_pattern(
    pattern_id: str, principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> PatternOut:
    actor = await resolve_coach_actor(session, principal, coach_id)
    return _out(await availability.get_owned_pattern(session, pattern_id, actor.coach_id))


@router.put("/{pattern_id}")
async def update_pattern(
    pattern_id: str,
    body: PatternBody,
    principal: CoachOrAdmin,
    session: Session,
    coach_id: CoachIdParam = None,
) -> PatternOut:
    actor = await resolve_coach_actor(session, principal, coach_id)
    pattern = await availability.get_owned_pattern(session, pattern_id, actor.coach_id)
    return _out(await availability.update_pattern(session, actor, pattern, body))


@router.delete("/{pattern_id}", status_code=204)
async def delete_pattern(
    pattern_id: str, principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> None:
    actor = await resolve_coach_actor(session, principal, coach_id)
    pattern = await availability.get_owned_pattern(session, pattern_id, actor.coach_id)
    await availability.delete_pattern(session, actor, pattern)


@router.post("/{pattern_id}/impact")
async def pattern_impact(
    pattern_id: str,
    principal: CoachOrAdmin,
    session: Session,
    body: PatternBody | None = None,
    coach_id: CoachIdParam = None,
) -> PatternImpact:
    """Dry-run: which booked appointments the proposed change (or deletion, empty body) cancels.

    Read-only despite the POST — the proposed pattern travels in the body, mirroring PUT.
    """
    actor = await resolve_coach_actor(session, principal, coach_id)
    pattern = await availability.get_owned_pattern(session, pattern_id, actor.coach_id)
    impacted = await availability.pattern_impact(session, pattern, body)
    return PatternImpact(pattern_id=pattern.id, cancelled_appointments=impacted)
