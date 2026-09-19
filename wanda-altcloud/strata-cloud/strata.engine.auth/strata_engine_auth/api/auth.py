"""The auth routes (mounted under /v1).

Routes stay thin: validate, call the Cognito seam, map errors. There is no
self-signup: accounts originate only from controlled origins
(the legacy import, the migration login, admin onboarding, or the dev seed).
``/me`` returns the enriched principal — profile fields joined with the
verified token's coarse roles — resolved strictly through the identity seam
and **failing hard** for a subject with no resolvable profile: lazy
provisioning is gone. Sign-in takes an ``identifier`` (a legacy app username
or an email address — the pool's email alias disambiguates), runs
the in-service migration branch for unknown users
(``services/migration.py``), and is the ONLY way clients authenticate; no
client ever calls Cognito directly (see architecture.md §2.2; doubly
load-bearing — the migration flow and the LEGACY error setting
both rely on it).
"""

from functools import partial
from typing import Annotated, Any

import anyio
import structlog
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import Language, UserProfile
from strata_identity.identifiers import UnmappedSubjectError, resolve_principal
from strata_identity.profiles import ProfileValidationError, update_profile
from strata_identity.schemas import UserProfileOut

from strata_engine_auth.api._errors import reraise
from strata_engine_auth.core.auth import CurrentPrincipal
from strata_engine_auth.core.config import settings
from strata_engine_auth.core.passwords import validate_password_policy
from strata_engine_auth.db.session import get_session
from strata_engine_auth.schemas.auth import (
    LoginRequest,
    PasswordPolicyOut,
    PasswordUpgradeRequest,
    PasswordUpgradeRequiredDetail,
    PasswordUpgradeRequiredResponse,
    RefreshRequest,
    TokenResponse,
    UpdateProfileRequest,
)
from strata_engine_auth.services import cognito
from strata_engine_auth.services.legacy import LegacyCredentialsClient, create_legacy_client
from strata_engine_auth.services.migration import (
    MigrationLoginFailed,
    PasswordUpgradeRequired,
    migrate,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_log = structlog.get_logger("strata_engine_auth.auth")

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_legacy_client = create_legacy_client(settings)


def get_legacy_client() -> LegacyCredentialsClient | None:
    """Dependency so tests can stub the legacy platform (TESTING.md: no live
    network); None ⇒ the migration branch is disabled (no STRATA_LEGACY_* env)."""
    return _legacy_client


LegacyDep = Annotated[LegacyCredentialsClient | None, Depends(get_legacy_client)]

# Documented error responses (the Cognito error mapping + bearer auth), so the
# OpenAPI contract — which the Schemathesis suite validates against — is honest.
# Login is deliberately narrower: every credential-dependent failure — wrong
# password, unknown user, every migration-branch leg — is the SAME generic 401
# (; the net's 404 enumeration signal died here at).
_LOGIN_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Invalid request (invalid parameter)"},
    401: {"description": "Invalid credentials (generic — reveals nothing)"},
    409: {
        "model": PasswordUpgradeRequiredResponse,
        "description": "Password upgrade required (— reachable ONLY with"
        " proven-valid legacy credentials for a migratable account)",
    },
}
_UPGRADE_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "Invalid request (invalid parameter, or the pool rejected"
        " the new password — the policy mirror drifted)"
    },
    401: {"description": "Invalid credentials (generic — reveals nothing)"},
    409: {
        "model": PasswordUpgradeRequiredResponse,
        "description": "The new password itself does not meet the pool password"
        " policy (checked first — no credential leaves the process)",
    },
}

# Built once from Settings: one static challenge body, byte-identical for every
# caller who reaches it (the 401's discipline, applied to 's sole exception).
_UPGRADE_DETAIL = PasswordUpgradeRequiredDetail(
    message="As part of the new platform onboarding, you must set a new password"
    " meeting the password policy.",
    password_policy=PasswordPolicyOut.from_settings(settings),
).model_dump()
_COGNITO_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Invalid request (invalid parameter)"},
    401: {"description": "Not authorised (wrong credentials or bad/expired token)"},
    403: {"description": "Forbidden (user not confirmed, or missing bearer token)"},
    404: {"description": "User not found"},
}
# Bearer-verified routes can also see a JWKS outage — an infrastructure failure
# surfaced as 503 by the shared seam (never a 401 that reads as session expiry) —
# and reject a verified subject with no resolvable profile.
_BEARER_ERRORS: dict[int | str, dict[str, Any]] = {
    **_COGNITO_ERRORS,
    403: {"description": "Forbidden (missing bearer token, or no profile for the subject)"},
    503: {"description": "Token verification temporarily unavailable (JWKS outage)"},
}
# The profile Save also raises a domain-level 422 (a ProfileValidationError:
# /2/3/9) whose body differs from FastAPI's default pydantic-validation 422,
# so the route declares it explicitly — keeping the OpenAPI contract the
# Schemathesis suite validates against honest (module docstring).
_PROFILE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_BEARER_ERRORS,
    422: {"description": "Profile validation error (2/3/9)"},
}


def _token_response(result: dict[str, Any]) -> TokenResponse:
    auth = result["AuthenticationResult"]
    return TokenResponse(
        access_token=auth["AccessToken"],
        id_token=auth["IdToken"],
        refresh_token=auth.get("RefreshToken"),  # absent in the refresh flow
        expires_in=auth["ExpiresIn"],
    )


def _generic_login_failure() -> HTTPException:
    # One body for every credential-dependent failure: a caller must not
    # be able to distinguish wrong-password / unknown / unmigrated / any
    # migration leg by status, message, or shape.
    return HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/login", responses=_LOGIN_ERRORS)
async def login(body: LoginRequest, session: SessionDep, legacy: LegacyDep) -> TokenResponse:
    """Sign in — with the in-service migration branch.

    ``InitiateAuth`` first; an existing user succeeds or fails right here (a
    wrong password never consults the legacy platform). Only the
    distinct unknown-user signal (``PreventUserExistenceErrors: LEGACY``)
    enters the migration branch: legacy check → gate → admin-create →
    adopt → one deterministic retry. Every failure leg returns the same
    generic 401; leg names go to the log with the correlation id, credentials
    and identifiers never do.
    """
    sign_in = partial(cognito.login, body.identifier, body.password)
    try:
        return _token_response(await anyio.to_thread.run_sync(sign_in))
    except ClientError as err:
        code = err.response["Error"]["Code"]
        if code == "InvalidParameterException":
            raise HTTPException(status_code=400, detail="Invalid request") from None
        if code != "UserNotFoundException" or legacy is None:
            if code not in ("NotAuthorizedException", "UserNotFoundException"):
                _log.warning("login_failed_unexpected_code", code=code)
            raise _generic_login_failure() from None

    try:
        await migrate(session, legacy, body.identifier, body.password)
    except PasswordUpgradeRequired as challenge:
        # / — the sole non-generic outcome, earned by legacy
        # acceptance + a passed gate. Nothing was created; the body is static.
        _log.info("migration_password_upgrade_required", drift=challenge.drift)
        raise HTTPException(status_code=409, detail=_UPGRADE_DETAIL) from None
    except MigrationLoginFailed as failure:
        _log.warning("migration_login_failed", leg=failure.leg)
        raise _generic_login_failure() from None

    try:  # the deterministic retry — the account and mapping now exist
        return _token_response(await anyio.to_thread.run_sync(sign_in))
    except ClientError:
        _log.warning("migration_login_retry_failed")
        raise _generic_login_failure() from None


@router.post("/password-upgrade", responses=_UPGRADE_ERRORS)
async def password_upgrade(
    body: PasswordUpgradeRequest, session: SessionDep, legacy: LegacyDep
) -> TokenResponse:
    """Complete a guided upgrade — statelessly.

    The second request earns migration on its own: the legacy password is
    re-proven and the gate re-run; the account is created with the compliant
    ``new_password``. The ``InitiateAuth`` probe comes first so an existing
    user's submit never reaches the legacy platform — and a NATIVE
    sign-in success is discarded and fails generically: this endpoint serves
    only the migration upgrade, never general password change.
    Every credential-dependent failure is the same generic 401.
    """
    # The new password's own policy check comes FIRST — before any credential
    # is checked anywhere — and answers with the same static challenge the
    # login sent (409 is part of this endpoint's documented contract; a 422
    # here would reject schema-valid input the OpenAPI contract accepts). It
    # depends only on the request body, so it reveals nothing about anyone.
    if validate_password_policy(body.new_password, settings):
        raise HTTPException(status_code=409, detail=_UPGRADE_DETAIL)

    probe = partial(cognito.login, body.identifier, body.password)
    try:
        await anyio.to_thread.run_sync(probe)
    except ClientError as err:
        code = err.response["Error"]["Code"]
        if code == "InvalidParameterException":
            raise HTTPException(status_code=400, detail="Invalid request") from None
        if code != "UserNotFoundException" or legacy is None:
            if code not in ("NotAuthorizedException", "UserNotFoundException"):
                _log.warning("password_upgrade_unexpected_code", code=code)
            raise _generic_login_failure() from None
    else:
        _log.warning("password_upgrade_not_eligible")  # already migrated, old pw live
        raise _generic_login_failure()

    try:
        await migrate(
            session, legacy, body.identifier, body.password, creation_password=body.new_password
        )
    except PasswordUpgradeRequired:
        # Only reachable as policy drift (the route already proved new_password
        # against the local mirror above). A 409 here would loop the caller
        # forever — it would advertise the very policy the caller just
        # satisfied; migration.py logged password_policy_drift for ops.
        raise HTTPException(status_code=400, detail="Invalid request") from None
    except MigrationLoginFailed as failure:
        _log.warning("password_upgrade_failed", leg=failure.leg)
        raise _generic_login_failure() from None

    # The deterministic retry — with the NEW password (also what converges the
    # double-submit race after a UsernameExists absorption).
    try:
        return _token_response(
            await anyio.to_thread.run_sync(
                partial(cognito.login, body.identifier, body.new_password)
            )
        )
    except ClientError:
        _log.warning("password_upgrade_retry_failed")
        raise _generic_login_failure() from None


@router.post("/refresh", responses=_COGNITO_ERRORS)
def refresh(body: RefreshRequest) -> TokenResponse:
    if settings.cognito_client_secret and not body.sub:
        # Fail loudly rather than send a SECRET_HASH keyed on the wrong value —
        # the hash must use the real Cognito username (the token's sub).
        raise HTTPException(
            status_code=400,
            detail="refresh with a confidential client requires sub"
            " (the token's subject — the real Cognito username)",
        )
    try:
        result = cognito.refresh(body.refresh_token, body.sub)
    except ClientError as err:
        reraise(err)
    return _token_response(result)


def _profile_out(profile: UserProfile, roles: list[str]) -> UserProfileOut:
    """The enriched-principal wire shape. ``display_name`` is the effective
    name existing consumers already read (override, else "First Last");
    the raw editable fields the profile page needs ride alongside it. Requires the
    ``user_languages`` relationship loaded (``language_codes`` derives from it)."""
    return UserProfileOut(
        id=profile.id,
        email=profile.email or "",
        first_name=profile.first_name,
        last_name=profile.last_name,
        display_name=profile.effective_display_name,
        display_name_override=profile.display_name,
        timezone=profile.timezone,
        preferred_language=profile.preferred_language_code,
        languages=profile.language_codes,
        roles=roles,
    )


@router.get("/me", responses=_BEARER_ERRORS)
async def me(principal: CurrentPrincipal, session: SessionDep) -> UserProfileOut:
    """The enriched principal: profile fields + the verified token's coarse roles.

    Resolution is strict: the seam's one rule — the active
    ``Cognito Sub`` mapping first, the profile id itself for dev tokens — and
    a verified subject with no profile is rejected, never provisioned.
    """
    try:
        profile = await resolve_principal(session, principal)
    except UnmappedSubjectError:
        # 403 mirrors the other services' fail-closed rejection (booking's
        # precedent); correlation ID rides the log line, no identifier values.
        _log.warning("me_unresolvable_subject")
        raise HTTPException(status_code=403, detail="No profile for this subject") from None
    # Spoken languages are the normalised ``user_languages`` links; the
    # resolver returns the profile without them, so load them before deriving
    # ``language_codes`` (async: no implicit lazy IO).
    await session.refresh(profile, attribute_names=["user_languages"])
    return _profile_out(profile, principal.roles)


@router.patch("/me", responses=_PROFILE_ERRORS)
async def update_me(
    body: UpdateProfileRequest, principal: CurrentPrincipal, session: SessionDep
) -> UserProfileOut:
    """The profile page's Save: persist the user's own server-held
    fields to the canonical kernel profile, then return the fresh enriched principal.

    Resolves through the same rule as ``/me`` (a user changes only their own
    profile); a verified subject with no profile is a 403, never provisioned.
    The seam enforces the domain invariants (2/3/9); a breach is a 422.
    """
    try:
        profile = await update_profile(
            session,
            principal,
            first_name=body.first_name,
            last_name=body.last_name,
            display_name=body.display_name,
            timezone=body.timezone,
            languages=body.languages,
            preferred_language=body.preferred_language,
        )
    except UnmappedSubjectError:
        _log.warning("update_me_unresolvable_subject")
        raise HTTPException(status_code=403, detail="No profile for this subject") from None
    except ProfileValidationError as err:
        raise HTTPException(status_code=422, detail=str(err)) from None
    await session.commit()
    return _profile_out(profile, principal.roles)


@router.get("/languages", responses=_BEARER_ERRORS)
async def languages(principal: CurrentPrincipal, session: SessionDep) -> list[str]:
    """The spoken-language catalogue: the ISO 639-1 codes the
    profile page renders as checkboxes. Read from the ``languages`` catalogue table
    so a future migration that adds a language surfaces here without a code change.
    Any authenticated user may read it (same guard as ``/me``); the coarse roles are
    irrelevant to a reference list."""
    result = await session.execute(select(Language.code).order_by(Language.code))
    return list(result.scalars().all())
