"""FastAPI dependencies over the verification seam.

A service builds one :class:`AuthSeam` at startup (around the verifier that
``create_verifier`` selected) and mounts its dependencies on routes:

    seam = AuthSeam(create_verifier(...))
    CurrentPrincipal = Annotated[Principal, Depends(seam.current_principal)]
    AdminOnly = Annotated[Principal, Depends(seam.require_role(ROLE_ADMIN))]
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from strata_identity.security.principal import Principal
from strata_identity.security.verifiers import TokenVerifier


class AuthSeam:
    def __init__(self, verifier: TokenVerifier) -> None:
        self._verifier = verifier

    def current_principal(self, request: Request) -> Principal:
        """The authenticated caller from the Authorization header (401 otherwise)."""
        auth = request.headers.get("Authorization", "")
        # RFC 7235: auth schemes are case-insensitive ("bearer x" is valid);
        # the token itself is passed through with its case untouched.
        scheme, sep, token = auth.partition(" ")
        if not sep or scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Missing bearer token")
        return self._verifier.verify(token)

    def require_role(self, *roles: str) -> Callable[..., Principal]:
        """Dependency factory: caller must hold one of the given catalogue roles (403)."""

        def guard(
            principal: Annotated[Principal, Depends(self.current_principal)],
        ) -> Principal:
            if not principal.has_role(*roles):
                raise HTTPException(status_code=403, detail="Insufficient role")
            return principal

        return guard
