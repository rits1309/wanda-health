"""Unavailability endpoints — one-off blocks and recurring patterns."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import UnavailabilityBlock, UnavailabilityPattern

from strata_booking.core.security import ROLE_ADMIN, ROLE_COACH, Principal, require_role
from strata_booking.db.session import get_session
from strata_booking.schemas.unavailability import (
    BlockBody,
    BlockOut,
    RecurringBody,
    RecurringOut,
)
from strata_booking.services import unavailability
from strata_booking.services.authz import resolve_coach_actor

_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Malformed request body"},
    401: {"description": "Not authenticated"},
    403: {"description": "Not permitted"},
    404: {"description": "Not found"},
}

blocks_router = APIRouter(
    prefix="/unavailability-blocks", tags=["unavailability"], responses=_RESPONSES
)
recurring_router = APIRouter(
    prefix="/unavailability-patterns", tags=["unavailability"], responses=_RESPONSES
)

CoachOrAdmin = Annotated[Principal, Depends(require_role(ROLE_COACH, ROLE_ADMIN))]
Session = Annotated[AsyncSession, Depends(get_session)]
CoachIdParam = Annotated[
    str | None, Query(description="Target coach (admins only; coaches act for themselves)")
]


def _block_out(b: UnavailabilityBlock) -> BlockOut:
    return BlockOut(
        id=b.id,
        coach_id=b.coach_id,
        date=b.date,
        start_time_local=b.start_time_local.isoformat(),
        end_time_local=b.end_time_local.isoformat(),
        timezone=b.timezone,
    )


def _recurring_out(p: UnavailabilityPattern) -> RecurringOut:
    return RecurringOut(
        id=p.id,
        coach_id=p.coach_id,
        days_of_week=p.days_of_week,
        start_time_local=p.start_time_local.isoformat(),
        end_time_local=p.end_time_local.isoformat(),
        timezone=p.timezone,
        active_from=p.active_from,
        active_to=p.active_to,
    )


@blocks_router.post("", status_code=201)
async def create_block(
    body: BlockBody, principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> BlockOut:
    actor = await resolve_coach_actor(session, principal, coach_id)
    return _block_out(await unavailability.create_block(session, actor, body))


@blocks_router.get("")
async def list_blocks(
    principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> list[BlockOut]:
    actor = await resolve_coach_actor(session, principal, coach_id)
    return [_block_out(b) for b in await unavailability.list_blocks(session, actor.coach_id)]


@blocks_router.delete("/{block_id}", status_code=204)
async def remove_block(
    block_id: str, principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> None:
    actor = await resolve_coach_actor(session, principal, coach_id)
    await unavailability.remove_block(session, actor, block_id)


@recurring_router.post("", status_code=201)
async def create_recurring(
    body: RecurringBody, principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> RecurringOut:
    actor = await resolve_coach_actor(session, principal, coach_id)
    return _recurring_out(await unavailability.create_recurring(session, actor, body))


@recurring_router.get("")
async def list_recurring(
    principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> list[RecurringOut]:
    actor = await resolve_coach_actor(session, principal, coach_id)
    return [_recurring_out(p) for p in await unavailability.list_recurring(session, actor.coach_id)]


@recurring_router.delete("/{pattern_id}", status_code=204)
async def remove_recurring(
    pattern_id: str, principal: CoachOrAdmin, session: Session, coach_id: CoachIdParam = None
) -> None:
    actor = await resolve_coach_actor(session, principal, coach_id)
    await unavailability.remove_recurring(session, actor, pattern_id)
