"""Programme-scoped authorisation (Halo).

Coaches act only for themselves. Admins act for any coach **in a programme they administer** —
the admin-to-coach relationship is derived from shared programme membership, never a direct
assignment. Every resolved action carries attribution: the acting user and, for
admin actions, the coach acted for. Programme membership comes from the kernel's
``programme_assignments`` — the token carries no programme claims.

Dispatch is **capability-based**, never a collapse of the multi-role principal to one role:
a Coach-and-Admin (the canonical cast ships one — Dana, the multi-role proof) self-serves as
a coach and reaches other coaches as an admin; each check asks "does the caller hold the role
this action needs", so single-role behaviour is untouched.
"""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import ROLE_BY_KIND, ProgrammeAssignment

from strata_booking.core.security import ROLE_ADMIN, ROLE_COACH, Principal


@dataclass(frozen=True)
class ActingContext:
    """Who is acting, on which coach's behalf — recorded on every side-effecting write."""

    coach_id: str
    acting_user_id: str
    on_behalf_of_coach_id: str | None  # set only when an admin acts for a coach


async def programme_ids_for(session: AsyncSession, user_kind: str, user_id: str) -> set[str]:
    """The programmes ``user_id`` belongs to wearing the given lowercase kind's hat.

    The one programme-membership query (booking, rescheduling and authz all
    share it) — a filter added here scopes every consumer identically.
    """
    return set(
        (
            await session.execute(
                select(ProgrammeAssignment.programme_id).where(
                    ProgrammeAssignment.assigned_as(ROLE_BY_KIND[user_kind]),
                    ProgrammeAssignment.user_id == user_id,
                )
            )
        )
        .scalars()
        .all()
    )


async def administered_programme_ids(session: AsyncSession, principal: Principal) -> set[str]:
    """The programmes this principal administers, from the kernel assignments."""
    return await programme_ids_for(session, "admin", principal.sub)


async def resolve_coach_actor(
    session: AsyncSession, principal: Principal, coach_id: str | None
) -> ActingContext:
    """Resolve a coach-scoped action to its target coach, enforcing programme-scope boundaries."""
    # Coach capability: self-service (no target, or explicitly themselves).
    if principal.has_role(ROLE_COACH) and coach_id in (None, principal.sub):
        own = principal.sub
        return ActingContext(coach_id=own, acting_user_id=own, on_behalf_of_coach_id=None)

    # Admin capability: act for a named coach in a shared administered programme.
    if principal.has_role(ROLE_ADMIN):
        if coach_id is None:
            raise HTTPException(
                status_code=422, detail="coach_id is required when an admin performs a coach action"
            )
        coach_programmes = await programme_ids_for(session, "coach", coach_id)
        if not coach_programmes & await administered_programme_ids(session, principal):
            raise HTTPException(
                status_code=403,
                detail="Admins may only act for coaches in programmes they administer",
            )
        return ActingContext(
            coach_id=coach_id, acting_user_id=principal.sub, on_behalf_of_coach_id=coach_id
        )

    if principal.has_role(ROLE_COACH):
        raise HTTPException(status_code=403, detail="Coaches may only act for themselves")
    raise HTTPException(status_code=403, detail="Insufficient role")
