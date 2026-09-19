"""Mint a dev bearer token from the command line (the `inv token` task).

Reads STRATA_DEV_AUTH_SECRET via Settings (.env), so tokens minted here verify against the
locally running service. Minted through the shared seam (strata.identity): the token
carries ``sub`` + the catalogue role only — identity and programme data comes from the kernel
tables, never from claims. Dev-only tooling — the AWS path never uses this seam.
"""

from __future__ import annotations

import argparse

from strata_core.domains.kernel import ROLE_BY_KIND
from strata_identity.security import mint_dev_token

from strata_booking.core.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a strata.booking dev bearer token.")
    parser.add_argument("--role", required=True, choices=["member", "coach", "admin"])
    parser.add_argument("--sub", required=True, help="subject id, e.g. c-1 / m-1 / a-1")
    args = parser.parse_args()

    if not settings.dev_auth_secret:
        raise SystemExit("STRATA_DEV_AUTH_SECRET is not set (see .env.example)")
    print(mint_dev_token(settings.dev_auth_secret, args.sub, [ROLE_BY_KIND[args.role]]))


if __name__ == "__main__":
    main()
