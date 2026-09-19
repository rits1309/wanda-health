"""Shipped testing helpers — for this package's suite AND its consumers' suites.

Consumers (``strata.engine.auth`` first) fake the same seam in their tests:
Cognito-shaped RS256 tokens verified against a *local* key (no network, no AWS).
These helpers are the single implementation, so the forgeries can never drift
from the real verifier's expectations. Test databases are bootstrapped with the
canonical migrations — ``strata_core.migrations`` — not by helpers here.

Dependency-light on purpose: only this package's runtime dependencies are used
(``cryptography`` arrives via ``pyjwt[crypto]``). Test-only libraries — pytest,
testcontainers — are **never** imported here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from strata_identity.security.verifiers import CognitoTokenVerifier

__all__ = [
    "generate_rsa_key",
    "local_jwks_client",
    "mint_cognito_token",
    "stub_jwks_client",
]


def generate_rsa_key() -> RSAPrivateKey:
    """A fresh RSA keypair for signing test tokens (generate once per session — slow)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def mint_cognito_token(
    private_key: RSAPrivateKey,
    *,
    sub: str,
    client_id: str,
    issuer: str,
    groups: Sequence[str] = (),
    token_use: str = "access",
    expires_in: timedelta = timedelta(hours=1),
    extra_claims: Mapping[str, Any] | None = None,
) -> str:
    """A locally signed RS256 token shaped like a Cognito access token.

    ``extra_claims`` is applied last, so it can override any default (e.g.
    ``{"sub": None}`` or a pre-expired ``exp``); ``expires_in`` may be negative
    to mint an already-expired token.
    """
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": sub,
        "token_use": token_use,
        "client_id": client_id,
        "iss": issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
        "cognito:groups": list(groups),
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, private_key, algorithm="RS256")


def local_jwks_client(private_key: RSAPrivateKey) -> SimpleNamespace:
    """A stand-in for ``PyJWKClient`` that resolves every token to the local key.

    Duck-typed via ``SimpleNamespace`` (the shape both suites already used), so
    it slots straight into ``CognitoTokenVerifier._jwks_client`` — directly or
    via ``monkeypatch.setattr``.
    """
    return SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private_key.public_key())
    )


def stub_jwks_client(verifier: CognitoTokenVerifier, private_key: RSAPrivateKey) -> None:
    """Point ``verifier`` at a local key — no network, no AWS, in any test."""
    verifier._jwks_client = local_jwks_client(private_key)  # type: ignore[assignment]
