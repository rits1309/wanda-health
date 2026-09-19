"""Mint a dev bearer token from the command line (the `inv token` task).

Reads STRATA_DEV_AUTH_SECRET via Settings (.env), so tokens minted here verify against the
locally running service. Minted through the shared seam (strata.identity): the token carries
``sub`` + the catalogue role only. This service accepts any authenticated user,
so the role is incidental — it exists so the same token also works against role-gated
services. Dev-only tooling — the AWS path never uses this seam.
"""

from __future__ import annotations

import argparse

from strata_identity.roles import ROLE_ADMIN, ROLE_COACH, ROLE_MEMBER
from strata_identity.security import mint_dev_token

from strata_terminology.core.config import settings

_ROLES = {"coach": ROLE_COACH, "member": ROLE_MEMBER, "admin": ROLE_ADMIN}


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a strata.terminology dev bearer token.")
    parser.add_argument("--role", default="coach", choices=sorted(_ROLES))
    parser.add_argument("--sub", default="t-1", help="subject id, e.g. c-1 / m-1 / a-1")
    args = parser.parse_args()

    if not settings.dev_auth_secret:
        raise SystemExit("STRATA_DEV_AUTH_SECRET is not set (see .env.example)")
    print(mint_dev_token(settings.dev_auth_secret, args.sub, [_ROLES[args.role]]))


if __name__ == "__main__":
    main()
