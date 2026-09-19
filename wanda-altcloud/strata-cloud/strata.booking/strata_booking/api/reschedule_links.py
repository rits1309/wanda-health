"""Public tokened reschedule-link endpoint.

No bearer auth: the signed, time-limited token in the emailed link is the credential. The page
shows the appointment and the same coach's bookable slots; fulfilment goes through the
authenticated member reschedule endpoint.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from strata_booking.db.session import get_session
from strata_booking.schemas.booking import RescheduleLinkOut
from strata_booking.services import rescheduling

_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Malformed request"},
    404: {"description": "Invalid link"},
    410: {"description": "Link expired, already used, or appointment no longer open"},
}

router = APIRouter(prefix="/reschedule", tags=["rescheduling"], responses=_RESPONSES)

Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("")
async def resolve(token: Annotated[str, Query()], session: Session) -> RescheduleLinkOut:
    return await rescheduling.resolve_link(session, token)
