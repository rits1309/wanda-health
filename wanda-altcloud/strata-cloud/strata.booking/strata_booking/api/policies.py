"""Cancellation policy endpoints. Policy writes are admin-only in Phase 1.

Configuration authority is an open question — the plan provisionally grants writes
to admins; confirm or restrict before production.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import CancellationPolicy

from strata_booking.core.security import ROLE_ADMIN, ROLE_COACH, Principal, require_role
from strata_booking.db.session import get_session
from strata_booking.schemas.policy import EffectivePolicyOut, PolicyBody, PolicyOut
from strata_booking.services import policy

_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "Malformed request body"},
    401: {"description": "Not authenticated"},
    403: {"description": "Not permitted"},
}

router = APIRouter(prefix="/cancellation-policies", tags=["policies"], responses=_RESPONSES)

Session = Annotated[AsyncSession, Depends(get_session)]
CoachOrAdmin = Annotated[Principal, Depends(require_role(ROLE_COACH, ROLE_ADMIN))]
AdminOnly = Annotated[Principal, Depends(require_role(ROLE_ADMIN))]


def _out(p: CancellationPolicy) -> PolicyOut:
    return PolicyOut(
        id=p.id,
        scope=p.scope,
        programme_id=p.programme_id,
        coach_id=p.coach_id,
        cancellation_window_hours=p.cancellation_window_hours,
        cancellation_allowed_by=p.cancellation_allowed_by,
        version=p.version,
        active=p.active,
    )


@router.get("")
async def list_policies(
    principal: CoachOrAdmin, session: Session, programme_id: Annotated[str, Query()]
) -> list[PolicyOut]:
    """All policy versions for a programme (history included; `active` marks the live ones)."""
    return [_out(p) for p in await policy.list_policies(session, programme_id)]


@router.get("/effective")
async def effective(
    principal: CoachOrAdmin,
    session: Session,
    programme_id: Annotated[str, Query()],
    coach_id: Annotated[str, Query()],
) -> EffectivePolicyOut:
    """The governing policy for a coach in a programme (coach override > programme > default)."""
    return policy.effective_out(await policy.effective_policy(session, programme_id, coach_id))


@router.post("", status_code=201)
async def create_policy(body: PolicyBody, principal: AdminOnly, session: Session) -> PolicyOut:
    return _out(await policy.create_policy(session, principal, body))
