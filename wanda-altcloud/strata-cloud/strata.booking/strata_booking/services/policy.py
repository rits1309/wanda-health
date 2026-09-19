"""Cancellation policies: versioned and immutable, coach override > programme.

Policies are never edited — a change creates a new version and deactivates the previous one.
When neither a coach- nor a programme-scoped policy exists, the platform default
applies (24 hours, cancellable by both parties).

Configuration authority note (open): Phase 1 provisionally grants policy writes to
admins for their programmes — to be confirmed or restricted in a later phase.
"""

from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, CancellationPolicy, Slot
from strata_core.domains.kernel import ProgrammeAssignment

from strata_booking.core.clock import clock
from strata_booking.core.security import Principal
from strata_booking.schemas.policy import EffectivePolicyOut, PolicyBody
from strata_booking.services.authz import administered_programme_ids

DEFAULT_WINDOW_HOURS = 24
DEFAULT_ALLOWED_BY = "both"


@dataclass(frozen=True)
class Effective:
    source: str  # coach | programme | default
    window_hours: int
    allowed_by: str


async def effective_policy(session: AsyncSession, programme_id: str, coach_id: str) -> Effective:
    coach_scoped = (
        await session.execute(
            select(CancellationPolicy).where(
                CancellationPolicy.scope == "coach",
                CancellationPolicy.programme_id == programme_id,
                CancellationPolicy.coach_id == coach_id,
                CancellationPolicy.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if coach_scoped is not None:
        return Effective(
            "coach", coach_scoped.cancellation_window_hours, coach_scoped.cancellation_allowed_by
        )
    programme_scoped = (
        await session.execute(
            select(CancellationPolicy).where(
                CancellationPolicy.scope == "programme",
                CancellationPolicy.programme_id == programme_id,
                CancellationPolicy.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if programme_scoped is not None:
        return Effective(
            "programme",
            programme_scoped.cancellation_window_hours,
            programme_scoped.cancellation_allowed_by,
        )
    return Effective("default", DEFAULT_WINDOW_HOURS, DEFAULT_ALLOWED_BY)


def effective_out(e: Effective) -> EffectivePolicyOut:
    return EffectivePolicyOut(
        source=e.source,
        cancellation_window_hours=e.window_hours,
        cancellation_allowed_by=e.allowed_by,
    )


async def create_policy(
    session: AsyncSession, principal: Principal, body: PolicyBody
) -> CancellationPolicy:
    """Create a new immutable policy version, deactivating the previous one (admin only)."""
    if body.programme_id not in await administered_programme_ids(session, principal):
        raise HTTPException(status_code=403, detail="Outside your programmes")
    if body.scope == "coach":
        coach_programmes = (
            (
                await session.execute(
                    select(ProgrammeAssignment.programme_id).where(
                        ProgrammeAssignment.assigned_as("Coach"),
                        ProgrammeAssignment.user_id == body.coach_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if body.programme_id not in coach_programmes:
            raise HTTPException(status_code=422, detail="Coach is not in that programme")

    previous = (
        await session.execute(
            select(CancellationPolicy).where(
                CancellationPolicy.scope == body.scope,
                CancellationPolicy.programme_id == body.programme_id,
                CancellationPolicy.coach_id == body.coach_id,
                CancellationPolicy.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    version = 1
    if previous is not None:
        previous.active = False
        version = previous.version + 1

    policy = CancellationPolicy(version=version, **body.model_dump())
    session.add(policy)
    await session.commit()
    return policy


async def list_policies(session: AsyncSession, programme_id: str) -> list[CancellationPolicy]:
    return list(
        (
            await session.execute(
                select(CancellationPolicy)
                .where(CancellationPolicy.programme_id == programme_id)
                .order_by(CancellationPolicy.scope, CancellationPolicy.version)
            )
        )
        .scalars()
        .all()
    )


async def validate_window(
    session: AsyncSession, appointment: Appointment, *, action: str = "Cancellation"
) -> None:
    """Raise 409 when the effective policy window has passed.

    The same window governs cancellation and rescheduling.
    """
    effective = await effective_policy(session, appointment.programme_id, appointment.coach_id)
    slot = await session.get(Slot, appointment.slot_id)
    if slot is None:
        raise HTTPException(status_code=500, detail="Appointment slot missing")
    deadline = slot.start_utc - timedelta(hours=effective.window_hours)
    if clock.now() > deadline:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{action} window has passed — changes must be made at least "
                f"{effective.window_hours} hours before the appointment"
            ),
        )


async def validate_cancellation(
    session: AsyncSession, appointment: Appointment, requester_kind: str
) -> None:
    """Cancellation checks: (1) requester allowed by policy; (2) inside the cancellation window.

    Raises 403 (not allowed to cancel) or 409 (window passed) with a clear reason. Admin
    actions count as the coach.
    """
    kind = "coach" if requester_kind == "admin" else requester_kind
    effective = await effective_policy(session, appointment.programme_id, appointment.coach_id)
    if effective.allowed_by != "both" and effective.allowed_by != kind:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Cancellation by {kind} is not permitted — the "
                f"{effective.source} policy allows: {effective.allowed_by}"
            ),
        )
    await validate_window(session, appointment)
