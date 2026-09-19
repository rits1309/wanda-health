"""The verification seam — consumed from strata.identity.

Booking is the seam's second consumer (after strata.engine.auth): token
verification, the ``Principal``, and the role guards all come from
``strata_identity.security``. Roles arrive as catalogue names (``Coach`` /
``Member`` / ``Admin``) mapped from the token's ``cognito:groups`` (dev tokens
carry the same shape); anything finer — programme membership, profile fields —
comes from the kernel tables, never from claims.

**The principal booking's routes receive is re-keyed onto its kernel profile**:
the token subject is an active ``Cognito Sub`` mapping in ``kernel.identifiers``
(Identity, — real pool tokens carry the Cognito UUID; the auth
seed adopts real subjects into the mapping), while every booking FK points at
``user_profiles.id``. The dependencies below resolve canonical-first (the
mapping, via the identity seam), fall back to the id itself (dev tokens are
historically minted with sub = the profile id), and fail closed (403) for a
verified subject with no profile — provisioning is the auth service's job,
never booking's.

Built at import time so a misconfigured verifier fails fast at startup. Dev
mode fails closed: ``create_verifier`` only sees an environment that was
EXPLICITLY configured (``STRATA_ENVIRONMENT`` set in the env/.env —
pydantic-settings records env-sourced fields in ``model_fields_set``), so the
Settings *default* of ``"local"`` can never enable dev mode by omission.
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from strata_identity.identifiers import UnmappedSubjectError, resolve_principal
from strata_identity.roles import ROLE_ADMIN, ROLE_COACH, ROLE_MEMBER
from strata_identity.security import AuthSeam, Principal, TokenVerifier, create_verifier

from strata_booking.core.config import Settings, settings
from strata_booking.db.session import get_session

__all__ = [
    "ROLE_ADMIN",
    "ROLE_COACH",
    "ROLE_MEMBER",
    "CurrentPrincipal",
    "Principal",
    "Role",
    "current_principal",
    "require_role",
    "seam",
]

#: Booking's recorded actor-kind vocabulary — what ``created_by``/``cancelled_by``
#: rows and policy ``allowed_by`` lists store (lowercase, one kind per action).
Role = Literal["member", "coach", "admin"]

_Session = Annotated[AsyncSession, Depends(get_session)]


def explicit_environment(s: Settings) -> str | None:
    """The configured environment, or None when STRATA_ENVIRONMENT was never set.

    ``Settings.environment`` defaults to ``"local"``; passing that default to
    ``create_verifier`` would let one *missing* env var switch dev mode on.
    ``model_fields_set`` only contains fields the env/.env actually provided.
    """
    return s.environment if "environment" in s.model_fields_set else None


def build_verifier(s: Settings) -> TokenVerifier:
    return create_verifier(
        s.auth_mode,
        environment=explicit_environment(s),
        region=s.cognito_region,
        user_pool_id=s.cognito_user_pool_id,
        client_id=s.cognito_client_id,
        dev_secret=s.dev_auth_secret,
    )


seam = AuthSeam(build_verifier(settings))


async def _domain_principal(session: AsyncSession, principal: Principal) -> Principal:
    """Re-key the verified principal's ``sub`` onto its ``user_profiles.id``.

    Resolution is the identity seam's ONE rule (`resolve_principal`, Identity
    — this function's original logic, promoted): the active ``Cognito Sub``
    mapping first, the profile id itself for dev tokens, fail closed on neither.
    """
    try:
        profile = await resolve_principal(session, principal)
    except UnmappedSubjectError:
        raise HTTPException(status_code=403, detail="No profile for this subject") from None
    return principal.model_copy(update={"sub": profile.id})


async def current_principal(request: Request, session: _Session) -> Principal:
    """FastAPI dependency: the verified caller, re-keyed onto its kernel profile."""
    return await _domain_principal(session, seam.current_principal(request))


def require_role(*roles: str) -> Callable[..., Coroutine[Any, Any, Principal]]:
    """Dependency factory: caller must hold one of the catalogue roles (403 otherwise)."""

    async def guard(request: Request, session: _Session) -> Principal:
        principal = seam.current_principal(request)
        if not principal.has_role(*roles):
            raise HTTPException(status_code=403, detail="Insufficient role")
        return await _domain_principal(session, principal)

    return guard


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
