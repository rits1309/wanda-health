"""Bearer-header helper — dev tokens minted through the shared seam.

Tokens carry ``sub`` + the catalogue role only; identity and programme data
comes from the seeded kernel rows, so tests authenticate as the fixture cast
(``c-1`` / ``m-1`` / ``a-1`` …) by id and the services look the rest up.
"""

import os

from strata_core.domains.kernel import ROLE_BY_KIND
from strata_identity.security import Principal, mint_dev_token


def bearer(sub: str, *kinds: str) -> dict[str, str]:
    """An Authorization header for ``sub`` holding the given lowercase kind(s).

    Multi-role principals are real (the canonical cast ships a Coach+Admin);
    pass several kinds to mint a token carrying all of them.
    """
    secret = os.environ["STRATA_DEV_AUTH_SECRET"]  # set by conftest before any test imports
    roles = [ROLE_BY_KIND[kind] for kind in kinds]
    return {"Authorization": f"Bearer {mint_dev_token(secret, sub, roles)}"}


def as_principal(sub: str, *kinds: str) -> Principal:
    """A service-level ``Principal`` for unit tests that drive a service seam directly."""
    return Principal(sub=sub, roles=[ROLE_BY_KIND[kind] for kind in kinds], token="")
