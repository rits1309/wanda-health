"""Coach calendar endpoint — all layers (available / booked / unavailable)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from strata_booking.core.security import ROLE_ADMIN, ROLE_COACH, Principal, require_role
from strata_booking.db.session import get_session
from strata_booking.schemas.unavailability import CoachCalendar
from strata_booking.services import calendar
from strata_booking.services.authz import resolve_coach_actor

router = APIRouter(
    prefix="/coaches",
    tags=["calendar"],
    responses={
        400: {"description": "Invalid query window"},
        401: {"description": "Not authenticated"},
        403: {"description": "Not permitted"},
        404: {"description": "Not found"},
    },
)


@router.get("/{coach_id}/calendar")
async def coach_calendar(
    coach_id: str,
    principal: Annotated[Principal, Depends(require_role(ROLE_COACH, ROLE_ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
) -> CoachCalendar:
    if to_date < from_date:
        raise HTTPException(status_code=400, detail="'to' must not be before 'from'")
    await resolve_coach_actor(session, principal, coach_id)
    return await calendar.coach_calendar(session, coach_id, from_date, to_date)
