"""Device registration at dispatch: through the identity write path.

Since Identity Mapping the mapping mechanics — idempotent
re-registration, supersede-for-audit (ended, never deleted,
), the partial-unique race backstop — live in the identifiers seam
(``strata_identity.identifiers.adopt``), the one write path for
``kernel.identifiers``. This service composes registration semantics on top:
the closed-catalogue and known-user validations, supersede reporting for the
API response, and device types stay NON-exclusive (a member may hold several
devices of one type). Readings already persisted under an ended mapping are
untouched.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import UserExternalId, UserProfile
from strata_identity.identifiers import UnknownIdentifierTypeError, active_mapping, adopt

from app.clock import clock


class UnknownIdentifierType(LookupError):
    """The named identifier type is not in the catalogue (a closed list)."""


class UnknownUser(LookupError):
    """The target Member does not exist in the identity kernel."""


@dataclass(frozen=True)
class RegistrationResult:
    mapping: UserExternalId
    identifier_type: str
    superseded_user_id: str | None


async def register_device(
    session: AsyncSession, *, user_id: str, identifier_type: str, external_id: str
) -> RegistrationResult:
    """Link a dispatched device to a Member; flushes but does not commit.

    Re-registering a device to the Member it is already actively mapped to returns the
    existing mapping unchanged — dispatch callers retry, and an at-least-once caller must
    not flip-flop the audit trail.
    """
    try:
        current = await active_mapping(session, type_name=identifier_type, external_id=external_id)
    except UnknownIdentifierTypeError:
        raise UnknownIdentifierType(identifier_type) from None

    user = (
        await session.execute(select(UserProfile).where(UserProfile.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise UnknownUser(user_id)

    superseded_user_id = (
        current.user_id if current is not None and current.user_id != user_id else None
    )
    mapping = await adopt(
        session,
        user_id=user_id,
        type_name=identifier_type,
        external_id=external_id,
        registered_at=clock.now(),
    )
    return RegistrationResult(mapping, identifier_type, superseded_user_id=superseded_user_id)
