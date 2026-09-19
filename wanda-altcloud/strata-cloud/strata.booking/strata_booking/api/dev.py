"""Dev-only endpoints — mounted under /v1 only when STRATA_DEV_MODE=true (never in AWS).

Phase 1 local-dev support: mint dev tokens for the seeded demo identities, inspect the stub
notification store (this is how reminder and suggested-alternatives emails are demonstrated),
and simulate Twilio call records for no-show detection.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import CallRecord, Notification
from strata_core.domains.kernel import ROLE_BY_KIND
from strata_identity.security import mint_dev_token as mint_seam_token

from strata_booking.core.clock import clock
from strata_booking.core.config import settings
from strata_booking.core.security import Role
from strata_booking.db.session import get_session
from strata_booking.services import jobs

router = APIRouter(
    prefix="/dev",
    tags=["dev"],
    responses={400: {"description": "Malformed request body"}},
)


class TokenRequest(BaseModel):
    sub: str
    role: Role
    # Accepted for back-compat with pre-seam clients (Summit's booking client, the
    # e2e stubs) and IGNORED since the seam swap: identity and programme
    # data now comes from the kernel tables, never from token claims.
    coach_id: str | None = None
    member_id: str | None = None
    programme_ids: list[str] = []


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/tokens")
def mint_dev_token(body: TokenRequest) -> TokenResponse:
    """Mint a dev token through the shared seam: ``sub`` + the catalogue role."""
    if not settings.dev_auth_secret:
        raise RuntimeError(
            "STRATA_DEV_AUTH_SECRET is not set — the dev auth seam needs it to mint/verify "
            "tokens (see .env.example)."
        )
    token = mint_seam_token(settings.dev_auth_secret, body.sub, [ROLE_BY_KIND[body.role]])
    return TokenResponse(access_token=token)


class DevNotification(BaseModel):
    id: str
    channel: str
    recipient_kind: str
    recipient_id: str
    notification_type: str
    payload: dict[str, object]
    created_at: datetime


@router.get("/notifications")
async def list_notifications(
    session: Annotated[AsyncSession, Depends(get_session)],
    recipient_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[DevNotification]:
    """Most recent stub notifications, newest first (optionally for one recipient)."""
    stmt = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if recipient_id is not None:
        stmt = stmt.where(Notification.recipient_id == recipient_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        DevNotification(
            id=n.id,
            channel=n.channel,
            recipient_kind=n.recipient_kind,
            recipient_id=n.recipient_id,
            notification_type=n.notification_type,
            payload=n.payload,
            created_at=n.created_at,
        )
        for n in rows
    ]


class JobRunResult(BaseModel):
    slots_created: int = 0
    reminders_sent: int = 0
    reminders_cancelled: int = 0
    no_shows_detected: int = 0
    appointments_completed: int = 0


@router.post("/jobs/run")
async def run_jobs(session: Annotated[AsyncSession, Depends(get_session)]) -> JobRunResult:
    """Run every due job once — the demo/test trigger for the jobs module."""
    return JobRunResult(**await jobs.run_all(session))


class CallRecordRequest(BaseModel):
    coach_id: str
    member_id: str
    initiated_at_utc: datetime | None = None  # defaults to "now"

    @field_validator("initiated_at_utc", mode="before")
    @classmethod
    def _rfc3339_string_only(cls, v: object) -> object:
        # The contract declares an RFC 3339 string; refuse epoch-number coercion.
        if v is not None and not isinstance(v, str | datetime):
            raise ValueError("initiated_at_utc must be an RFC 3339 date-time string")
        return v


class CallRecordResponse(BaseModel):
    id: str
    initiated_at_utc: datetime


@router.post("/call-records")
async def create_call_record(
    body: CallRecordRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CallRecordResponse:
    """Simulate a Twilio call having been initiated (feeds no-show detection)."""
    record = CallRecord(
        coach_id=body.coach_id,
        member_id=body.member_id,
        initiated_at_utc=body.initiated_at_utc or clock.now(),
    )
    session.add(record)
    await session.commit()
    return CallRecordResponse(id=record.id, initiated_at_utc=record.initiated_at_utc)
