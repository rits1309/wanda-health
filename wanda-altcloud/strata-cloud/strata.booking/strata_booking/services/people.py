"""People lookups over the canonical kernel.

Booking's local ``coaches``/``members``/``admins`` stand-ins are gone: people
are the kernel's ``user_profiles`` and *which hat someone wears in a programme*
is the kernel assignment's ``role_id`` (``ProgrammeAssignment.assigned_as``).
Coaches need no view of their own — ``UserProfile`` carries the name (rendered
through ``effective_display_name``: the optional override, else "First Last" —
)/``timezone`` and its spoken languages (the ``user_languages`` links,
).
The preferred language is the person-level ``preferred_language_code`` FK on
the profile; where it was never chosen the first spoken language is used,
defaulting to ``"en"`` — the impact report's fallback, re-derived on the
normalised shape.

Role-ness is enforced here at the service level, never by DDL.

The scoped members listing lives here too: the same query shape as
the coach listings (``assigned_as`` over the caller's programmes), returning
the member view booking's staff surfaces consume.
"""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from strata_core.domains.kernel import ProgrammeAssignment, UserProfile

from strata_booking.core.security import ROLE_ADMIN, ROLE_COACH, Principal
from strata_booking.schemas.members import MemberOut
from strata_booking.services.authz import (
    administered_programme_ids,
    programme_ids_for,
    resolve_coach_actor,
)


@dataclass(frozen=True)
class MemberView:
    """The member shape booking's services consume — profile + language facts."""

    id: str
    display_name: str
    timezone: str
    languages: list[str]
    preferred_language: str


def _member_view(profile: UserProfile) -> MemberView:
    """The view over a loaded profile — preferred falls back first spoken, then ``en``."""
    languages = profile.language_codes
    preferred = profile.preferred_language_code or (languages[0] if languages else "en")
    return MemberView(
        id=profile.id,
        display_name=profile.effective_display_name,
        timezone=profile.timezone,
        languages=languages,
        preferred_language=preferred,
    )


async def find_member(session: AsyncSession, member_id: str) -> MemberView | None:
    profile = (
        await session.execute(
            select(UserProfile)
            .options(selectinload(UserProfile.user_languages))
            .where(UserProfile.id == member_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        return None
    return _member_view(profile)


async def get_member(session: AsyncSession, member_id: str) -> MemberView:
    member = await find_member(session, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _effective_display_name() -> ColumnElement[str]:
    """The effective name as one SQL expression — the override, else "First Last".

    Shared by the batched name lookup and the picker ordering so the formula lives
    in one place at the SQL layer (the ORM-instance form is
    ``UserProfile.effective_display_name``).
    """
    return func.coalesce(
        UserProfile.display_name,
        UserProfile.first_name + " " + UserProfile.last_name,
    )


async def display_names_for(session: AsyncSession, user_ids: set[str]) -> dict[str, str]:
    """Kernel effective display names for ``user_ids``, one query (3.2 batching).

    Effective per: the optional ``display_name`` override when
    set, else "First Last". Missing profiles are simply absent — callers fall
    back to the raw id (``names.get(user_id, user_id)``, the ``alternatives``
    precedent), so a dangling reference degrades to today's behaviour instead
    of failing.
    """
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(UserProfile.id, _effective_display_name()).where(
                UserProfile.id.in_(sorted(user_ids))
            )
        )
    ).tuples()
    return {profile_id: name for profile_id, name in rows}


async def list_members(
    session: AsyncSession, principal: Principal, coach_id: str | None = None
) -> list[MemberOut]:
    """The members of the programmes the caller's capability reaches.

    Coach capability reaches the coach's own programmes; admin capability the
    administered ones; a caller holding both gets the union (the
    ``list_appointments_scoped`` shape). ``coach_id`` narrows to the acted-for
    coach's programmes through the shared-programme check, capped at the acting
    admin's administered set (— a scoped admin never reaches members of a
    programme they do not administer, even via a shared coach), so every listed
    member is bookable by that coach within the caller's remit. Membership comes
    from the kernel's ``programme_assignments``, never token claims;
    ordering is by the effective display name — override else "First Last",
    / — (id as the tie-break) for a stable picker.
    """
    if coach_id is not None:
        acting = await resolve_coach_actor(session, principal, coach_id)
        programmes = await programme_ids_for(session, "coach", acting.coach_id)
        if acting.on_behalf_of_coach_id is not None:
            programmes &= await administered_programme_ids(session, principal)
    else:
        programmes = set()
        if principal.has_role(ROLE_COACH):
            programmes |= await programme_ids_for(session, "coach", principal.sub)
        if principal.has_role(ROLE_ADMIN):
            programmes |= await programme_ids_for(session, "admin", principal.sub)
    if not programmes:
        return []
    member_ids = select(ProgrammeAssignment.user_id).where(
        ProgrammeAssignment.programme_id.in_(sorted(programmes)),
        ProgrammeAssignment.assigned_as("Member"),
    )
    profiles = (
        (
            await session.execute(
                select(UserProfile)
                .options(selectinload(UserProfile.user_languages))
                .where(UserProfile.id.in_(member_ids))
                .order_by(_effective_display_name(), UserProfile.id)
            )
        )
        .scalars()
        .all()
    )
    return [_member_out(profile) for profile in profiles]


def _member_out(profile: UserProfile) -> MemberOut:
    """The wire model, exactly 's field set — built here so the router stays thin."""
    view = _member_view(profile)
    return MemberOut(
        id=view.id,
        display_name=view.display_name,
        preferred_language=view.preferred_language,
        timezone=view.timezone,
    )
