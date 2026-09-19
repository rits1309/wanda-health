"""Role assignment — the database half of the role sync rule.

``user_roles`` is the authoritative assignment source; the matching Cognito
group membership mirrors it. Anything that assigns or revokes a role calls
these functions AND the Cognito group operation together (the dev seed script
in Phase 1; admin flows in a later phase). The group names are the lowercase
counterparts of the catalogue names, mapped here so the verifier and the
seed share one vocabulary.
"""

from typing import Any, cast

from sqlalchemy import CursorResult, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.db.common import new_id
from strata_core.domains.kernel import ROLE_NAMES, Role, UserRole

# The catalogue vocabulary is the kernel's (single definition).
ROLE_COACH, ROLE_MEMBER, ROLE_ADMIN = ROLE_NAMES

__all__ = [
    "GROUP_BY_ROLE",
    "ROLE_ADMIN",
    "ROLE_BY_GROUP",
    "ROLE_COACH",
    "ROLE_MEMBER",
    "ROLE_NAMES",
    "assign_role",
    "revoke_role",
    "roles_for",
]

# Cognito group (token claim vocabulary) per catalogue role.
GROUP_BY_ROLE = {ROLE_COACH: "coach", ROLE_MEMBER: "member", ROLE_ADMIN: "admin"}
ROLE_BY_GROUP = {group: role for role, group in GROUP_BY_ROLE.items()}


async def _role_by_name(session: AsyncSession, role_name: str) -> Role:
    if role_name not in ROLE_NAMES:
        raise ValueError(f"Unknown role {role_name!r}; expected one of {ROLE_NAMES}")
    role = (await session.execute(select(Role).where(Role.name == role_name))).scalar_one()
    return role


async def assign_role(session: AsyncSession, user_id: str, role_name: str) -> bool:
    """Assign a catalogue role to a user. Idempotent — False if already held.

    INSERT ... ON CONFLICT DO NOTHING so a concurrent assign of the same pair
    lands as a clean False, not an IntegrityError.
    """
    role = await _role_by_name(session, role_name)
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            pg_insert(UserRole)
            .values(id=new_id(), user_id=user_id, role_id=role.id)
            .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
        ),
    )
    return bool(result.rowcount)


async def revoke_role(session: AsyncSession, user_id: str, role_name: str) -> bool:
    """Revoke a catalogue role from a user. Idempotent: False if not held."""
    role = await _role_by_name(session, role_name)
    existing = (
        await session.execute(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        return False
    await session.delete(existing)
    await session.flush()
    return True


async def roles_for(session: AsyncSession, user_id: str) -> list[str]:
    """The catalogue role names a user holds, sorted for determinism."""
    names = (
        (
            await session.execute(
                select(Role.name)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    return sorted(names)
