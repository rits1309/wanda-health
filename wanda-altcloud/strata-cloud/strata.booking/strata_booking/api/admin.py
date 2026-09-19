"""Admin endpoints: the combined multi-coach calendar.

Admin *write* capabilities live on the resource endpoints themselves — every coach-scoped write
resolves through :func:`strata_booking.services.authz.resolve_coach_actor` (or scopes by the
appointment's programme) and records act-for-coach attribution on audit fields and event
payloads. This router only adds the admin-shaped read.
"""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from strata_booking.core.security import ROLE_ADMIN, Principal, require_role
from strata_booking.db.session import get_session
from strata_booking.schemas.unavailability import AdminCalendar
from strata_booking.services import calendar

_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Invalid query window"},
    401: {"description": "Not authenticated"},
    403: {"description": "Not permitted / coach outside your programmes"},
    422: {"description": "Validation error"},
}

router = APIRouter(prefix="/admin", tags=["admin"], responses=_RESPONSES)

Layer = Literal["available", "booked", "unavailable"]


@router.get("/calendar")
async def admin_calendar(
    principal: Annotated[Principal, Depends(require_role(ROLE_ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_date: Annotated[date, Query(alias="from")],
    to_date: Annotated[date, Query(alias="to")],
    coach_ids: Annotated[list[str] | None, Query()] = None,
    layers: Annotated[list[Layer] | None, Query()] = None,
) -> AdminCalendar:
    """Combined calendar: coach multi-select + per-layer toggles (defaults: all/all)."""
    if to_date < from_date:
        raise HTTPException(status_code=400, detail="'to' must not be before 'from'")
    return await calendar.admin_calendar(
        session,
        principal,
        coach_ids=coach_ids,
        from_date=from_date,
        to_date=to_date,
        layers=list(layers) if layers else None,
    )
