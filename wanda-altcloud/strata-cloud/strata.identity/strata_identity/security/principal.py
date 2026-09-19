"""The authenticated caller, as every Savanna service sees it.

Claims-derived only: ``sub`` and the coarse catalogue role names mapped from the
token's ``cognito:groups`` ( — coarse roles ride in the token; anything
finer comes from the database). Profile enrichment (display name, timezone) is
the auth service's job (``GET /v1/auth/me``), not the verifier's.
"""

from pydantic import BaseModel, Field


class Principal(BaseModel):
    """The verified caller."""

    sub: str
    roles: list[str] = Field(default=[], description="Catalogue role names, e.g. ['Coach']")
    # From the token's `email` claim when present. Cognito ACCESS tokens carry no
    # email claim, so this is None in cognito mode; dev tokens minted with
    # `mint_dev_token(..., email=...)` populate it (offline profile provisioning).
    email: str | None = None
    # Not carried by tokens: None off the verifier; the auth service's
    # profile enrichment may populate them after verification.
    display_name: str | None = None
    timezone: str | None = None
    # The raw bearer token, kept for user-scoped Cognito calls (e.g. GetUser in /me).
    token: str = Field(repr=False, exclude=True)

    def has_role(self, *names: str) -> bool:
        return any(name in self.roles for name in names)
