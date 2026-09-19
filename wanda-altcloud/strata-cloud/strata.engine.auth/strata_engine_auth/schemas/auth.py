"""Pydantic request/response models — the API contract.

No signup/confirm shapes: self-registration is retired.
Sign-in takes an ``identifier`` — a legacy app username or an email address
in the same field (the pool's email alias disambiguates). The
password-upgrade shapes carry 's guided upgrade (Identity): the 409
challenge echoes the CONFIGURED policy — one static body, never the caller's
own unmet requirements. ``new_password`` is deliberately a plain string here:
its policy check lives in the route (first, before any credential leaves the
process) and answers with the same static 409 challenge, keeping the OpenAPI
schema honest — schema-valid requests are never rejected with a shape the
contract doesn't carry.
"""

from typing import Literal, Self

from pydantic import BaseModel, EmailStr, Field

from strata_engine_auth.core.config import Settings


class LoginRequest(BaseModel):
    identifier: str = Field(
        min_length=1, description="Username or email address (one field, email alias)"
    )
    password: str


class PasswordPolicyOut(BaseModel):
    """The pool password policy, echoed structurally (values from Settings —
    deployment-static, identical for every caller)."""

    min_length: int
    require_uppercase: bool
    require_lowercase: bool
    require_digit: bool
    require_symbol: bool

    @classmethod
    def from_settings(cls, source: Settings) -> Self:
        return cls(
            min_length=source.password_min_length,
            require_uppercase=source.password_require_uppercase,
            require_lowercase=source.password_require_lowercase,
            require_digit=source.password_require_digit,
            require_symbol=source.password_require_symbol,
        )


class PasswordUpgradeRequiredDetail(BaseModel):
    """The 409 challenge — reachable ONLY with proven-valid
    legacy credentials for a migratable account, so it reveals nothing a caller
    does not already hold."""

    code: Literal["password_upgrade_required"] = "password_upgrade_required"
    message: str
    password_policy: PasswordPolicyOut


class PasswordUpgradeRequiredResponse(BaseModel):
    """Mirrors FastAPI's ``HTTPException`` wrapper so the documented 409 model
    matches the wire shape (the Schemathesis suite validates against it)."""

    detail: PasswordUpgradeRequiredDetail


class PasswordUpgradeRequest(BaseModel):
    identifier: str = Field(
        min_length=1, description="Username or email address (one field, email alias)"
    )
    # The legacy password — re-proven against the legacy platform, deliberately
    # never policy-checked (it predates the policy; that is the point).
    password: str
    new_password: str = Field(
        description="Replacement password; must meet the pool password policy"
        " (— a non-compliant one gets the same 409 challenge as login)"
    )


class RefreshRequest(BaseModel):
    """Refresh-flow request.

    With a confidential client (a client secret configured), ``sub`` — the
    token's subject, i.e. the user's REAL Cognito username — is required to
    compute ``SECRET_HASH``. ``email`` is kept for wire compatibility only: it
    is NOT the Cognito username, so it is never used for hashing (a hash keyed
    on the email would break every refresh).
    """

    refresh_token: str
    # Only required if the app client has a secret (keys SECRET_HASH).
    sub: str | None = None
    # Deprecated: kept so existing clients' payloads still validate; ignored.
    email: EmailStr | None = None


class TokenResponse(BaseModel):
    access_token: str
    id_token: str
    refresh_token: str | None = None
    expires_in: int
    token_type: str = "Bearer"


# /me returns strata_identity.schemas.UserProfileOut — the shared enriched-principal
# wire type (profile fields + coarse roles), owned by the identity package.


class UpdateProfileRequest(BaseModel):
    """The profile page's Save (PATCH /v1/auth/me): the full set of
    server-held mutable fields, submitted together. Shape (types) is checked here;
    the domain invariants (non-blank names, at least one spoken language,
    preferred-among-spoken, timezone in the closed list) are enforced by
    the identity seam's ``update_profile`` so every writer applies them identically.
    Immutable identity (id, email, roles, the Cognito mapping) is not accepted here.
    """

    first_name: str
    last_name: str
    display_name: str | None = None
    timezone: str
    languages: list[str]
    preferred_language: str | None = None
