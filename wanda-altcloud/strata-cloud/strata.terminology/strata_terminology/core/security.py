"""The verification seam — consumed from strata.identity.

Terminology serves non-PHI reference lookups, so the bar is authentication, not
authorisation: every ``/v1`` route requires a valid bearer
token — **any authenticated user** — and no role guard is applied. The principal
is used claims-only (``sub`` + roles); nothing here reads the kernel tables, so
the dependency takes no database session.

Built at import time so a misconfigured verifier fails fast at startup. Dev
mode fails closed: ``create_verifier`` only sees an environment that was
EXPLICITLY configured (``STRATA_ENVIRONMENT`` set in the env/.env —
pydantic-settings records env-sourced fields in ``model_fields_set``), so the
Settings *default* of ``"local"`` can never enable dev mode by omission.
"""

from typing import Annotated

from fastapi import Depends, Request
from strata_identity.security import AuthSeam, Principal, TokenVerifier, create_verifier

from strata_terminology.core.config import Settings, settings

__all__ = ["CurrentPrincipal", "Principal", "current_principal", "seam"]


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


def current_principal(request: Request) -> Principal:
    """FastAPI dependency: the verified caller (claims-only; any authenticated user)."""
    return seam.current_principal(request)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
