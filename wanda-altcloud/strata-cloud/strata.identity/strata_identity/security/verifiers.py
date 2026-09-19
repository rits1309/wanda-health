"""Token verification — one seam for every Savanna service.

Two interchangeable verifiers behind ``STRATA_AUTH_MODE``:

- ``cognito`` — RS256 access tokens from the single platform user pool
, verified against the pool's cached JWKS; coarse roles mapped from
  the ``cognito:groups`` claim (unknown groups are ignored).
- ``dev`` — locally minted HS256 tokens (``mint_dev_token``) with the same
  claim shape; **local development only** — :func:`create_verifier` refuses dev
  mode unless the environment is *explicitly* ``"local"`` (an unset
  ``STRATA_ENVIRONMENT`` — ``None`` here — fails closed). Compatible with
  ``strata.booking``'s Phase-1 claim shape (a single lowercase ``role`` claim
  is accepted as a one-element ``roles``), so the later booking swap is
  mechanical. Dev tokens **must carry ``exp``** — a signed token without an
  expiry is rejected (PyJWT only validates ``exp`` when present, so it is
  required explicitly).

Verification failures raise ``fastapi.HTTPException(401)`` — the seam is
consumed as FastAPI dependencies (see :mod:`strata_identity.security.dependencies`).
A JWKS fetch failure (infrastructure, not the caller's token) raises 503 so an
outage never reads as session expiry.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient

from strata_identity.roles import ROLE_BY_GROUP, ROLE_NAMES
from strata_identity.security.principal import Principal

AuthMode = Literal["cognito", "dev"]

_DEV_ALGORITHM = "HS256"
_DEV_DEFAULT_TTL = timedelta(hours=12)


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail)


def _roles_from_claims(claims: dict[str, Any]) -> list[str]:
    """Map token claims to catalogue role names.

    ``cognito:groups`` / ``roles`` carry group-vocabulary or catalogue names;
    booking's Phase-1 single ``role`` claim is accepted as one role. Unknown
    values are ignored (a group that is not a role must not break login).
    """
    raw: list[str] = list(claims.get("cognito:groups") or claims.get("roles") or [])
    if not raw and (single := claims.get("role")):
        raw = [single]
    mapped = [ROLE_BY_GROUP.get(value, value) for value in raw]
    return sorted({name for name in mapped if name in ROLE_NAMES})


def _principal_from_claims(claims: dict[str, Any], token: str) -> Principal:
    """One construction point for both verifiers — dev and cognito modes must never diverge."""
    sub = claims.get("sub")
    if not sub:
        # Validly signed but subject-less (e.g. a wrong-shaped token): an auth
        # rejection (401), never a KeyError-turned-500.
        raise _unauthorised("Token has no subject")
    # Cognito ACCESS tokens carry no email claim (email stays None in cognito
    # mode); dev tokens minted with email= populate it for offline provisioning.
    return Principal(
        sub=sub, roles=_roles_from_claims(claims), email=claims.get("email"), token=token
    )


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class CognitoTokenVerifier:
    """Verifies Cognito access tokens (RS256) against the pool JWKS."""

    def __init__(self, region: str, user_pool_id: str, client_id: str) -> None:
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.client_id = client_id
        # Fetches + caches the pool's public keys on first use.
        self._jwks_client = PyJWKClient(f"{self.issuer}/.well-known/jwks.json")

    def verify(self, token: str) -> Principal:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError as exc:
            # A JWKS *fetch* failure (outage, DNS, timeout) is infrastructure,
            # not the caller's token — 503, never a 401 that reads as session
            # expiry. Caught first: it subclasses PyJWTError. Other JWKS errors
            # (e.g. a kid the pool doesn't know — a foreign or forged token)
            # ARE a verdict on the token and stay 401 below.
            raise HTTPException(
                status_code=503, detail="Token verification temporarily unavailable"
            ) from exc
        except jwt.PyJWTError as exc:
            raise _unauthorised("Invalid or expired token") from exc
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                # Access tokens carry `client_id`, not `aud`; checked manually below.
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            raise _unauthorised("Invalid or expired token") from exc

        if claims.get("token_use") != "access" or claims.get("client_id") != self.client_id:
            raise _unauthorised("Token not valid for this client")
        return _principal_from_claims(claims, token)


class DevTokenVerifier:
    """Verifies locally minted HS256 dev tokens (never deployed — see create_verifier)."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise RuntimeError("STRATA_DEV_AUTH_SECRET is not set — dev-mode verification needs it")
        self.secret = secret

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=[_DEV_ALGORITHM],
                # PyJWT only validates exp when present; an expiry-less token
                # would otherwise live forever, so require the claim outright.
                options={"require": ["exp"]},
            )
        except jwt.PyJWTError as exc:
            raise _unauthorised("Invalid or expired token") from exc
        return _principal_from_claims(claims, token)


def mint_dev_token(
    secret: str,
    sub: str,
    roles: list[str],
    ttl: timedelta = _DEV_DEFAULT_TTL,
    *,
    email: str | None = None,
) -> str:
    """Mint a signed dev token (local development and tests only).

    ``email`` adds an ``email`` claim so dev-mode consumers can provision a
    profile offline (Cognito's GetUser is unreachable for dev tokens).
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": sub,
        "roles": roles,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, secret, algorithm=_DEV_ALGORITHM)


def create_verifier(
    mode: AuthMode,
    *,
    environment: str | None,
    region: str | None = None,
    user_pool_id: str | None = None,
    client_id: str | None = None,
    dev_secret: str | None = None,
) -> TokenVerifier:
    """Build the verifier for ``STRATA_AUTH_MODE``; dev mode refuses to leave localhost.

    ``environment`` must be the *explicitly configured* environment — pass
    ``None`` when the deployment did not set one (e.g. ``STRATA_ENVIRONMENT``
    unset, even if the consumer's Settings would default it). Dev mode fails
    closed: a missing environment is refused exactly like a non-local one.
    """
    if mode == "cognito":
        if not (region and user_pool_id and client_id):
            raise RuntimeError(
                "cognito auth mode needs STRATA_COGNITO_REGION, STRATA_COGNITO_USER_POOL_ID,"
                " and STRATA_COGNITO_CLIENT_ID"
            )
        return CognitoTokenVerifier(region, user_pool_id, client_id)
    if environment is None or environment != "local":
        raise RuntimeError(
            "STRATA_AUTH_MODE=dev requires STRATA_ENVIRONMENT to be EXPLICITLY set to"
            f" 'local' (got {environment!r}); an unset environment fails closed —"
            " dev mode never starts on defaults"
        )
    return DevTokenVerifier(dev_secret or "")
