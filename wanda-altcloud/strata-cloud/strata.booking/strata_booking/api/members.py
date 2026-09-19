"""The scoped members listing — who the caller can book on behalf of."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from strata_booking.core.security import ROLE_ADMIN, ROLE_COACH, Principal, require_role
from strata_booking.db.session import get_session
from strata_booking.schemas.members import MemberOut
from strata_booking.services import people

router = APIRouter(
    prefix="/members",
    tags=["members"],
    responses={
        400: {"description": "Malformed request"},
        401: {"description": "Not authenticated"},
        403: {"description": "Not permitted"},
    },
)


@router.get("")
async def list_members(
    principal: Annotated[Principal, Depends(require_role(ROLE_COACH, ROLE_ADMIN))],
    session: Annotated[AsyncSession, Depends(get_session)],
    coach_id: str | None = None,
) -> list[MemberOut]:
    return await people.list_members(session, principal, coach_id=coach_id)
