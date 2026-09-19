"""Signed, time-limited link tokens and shared client-link constants.

Tokens are HS256 JWTs scoped to a purpose + reference id, minted for notification links and
verified on the public tokened endpoints. Only a SHA-256 hash is stored server-side; the raw
token exists solely inside the emailed link.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import jwt
from fastapi import HTTPException

from strata_booking.core.clock import clock
from strata_booking.core.config import settings

BOOKING_PAGE_LINK = "/book"  # client route carried in notifications for self-service rebooking

_ALGORITHM = "HS256"


def _secret() -> str:
    if not settings.dev_auth_secret:
        raise RuntimeError("STRATA_DEV_AUTH_SECRET is not set — required to sign link tokens.")
    return settings.dev_auth_secret


def mint_link_token(purpose: str, ref_id: str, ttl: timedelta) -> str:
    now = clock.now()
    payload = {
        "purpose": purpose,
        "ref": ref_id,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def verify_link_token(token: str, purpose: str) -> str:
    """Return the reference id; 410 on expiry, 404 on anything else invalid.

    Expiry is checked against the service :mod:`clock` seam (the platform's single time
    authority), not the host wall clock — signature verification stays with PyJWT.
    """
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=[_ALGORITHM],
            # All time-based claims are judged against the clock seam below, never the host
            # wall clock (PyJWT would otherwise reject iat/exp minted under a pinned clock).
            options={"verify_exp": False, "verify_iat": False, "verify_nbf": False},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=404, detail="Invalid link") from exc
    if payload.get("purpose") != purpose:
        raise HTTPException(status_code=404, detail="Invalid link")
    if int(payload.get("exp", 0)) < int(clock.now().timestamp()):
        raise HTTPException(status_code=410, detail="This link has expired")
    return str(payload["ref"])


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
