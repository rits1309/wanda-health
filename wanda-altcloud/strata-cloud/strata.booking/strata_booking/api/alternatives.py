"""Public tokened endpoints for suggested alternatives.

No bearer auth: the signed, time-limited token in the emailed link is the credential. The
screen is accept-or-reject only — details cannot be altered.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from strata_booking.db.session import get_session
from strata_booking.services import alternatives

_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Malformed request body"},
    404: {"description": "Invalid link"},
    409: {"description": "Slot since taken / already accepted"},
    410: {"description": "Link expired or suggestion closed"},
}

router = APIRouter(prefix="/alternatives", tags=["alternatives"], responses=_RESPONSES)

Session = Annotated[AsyncSession, Depends(get_session)]


class AlternativeDetails(BaseModel):
    status: str
    slot_available: bool
    coach_id: str
    coach_name: str
    start_utc: datetime
    end_utc: datetime
    duration_minutes: int
    booking_page_link: str


class TokenBody(BaseModel):
    token: str


class AcceptedOut(BaseModel):
    appointment_id: str


@router.get("")
async def describe(token: Annotated[str, Query()], session: Session) -> AlternativeDetails:
    return AlternativeDetails(**await alternatives.describe(session, token))


@router.post("/accept")
async def accept(body: TokenBody, session: Session) -> AcceptedOut:
    return AcceptedOut(appointment_id=await alternatives.accept(session, body.token))


@router.post("/reject", status_code=204)
async def reject(body: TokenBody, session: Session) -> None:
    await alternatives.reject(session, body.token)
