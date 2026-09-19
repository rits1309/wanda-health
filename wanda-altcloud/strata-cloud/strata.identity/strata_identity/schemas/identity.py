"""Wire types shared by every service that returns identity data.

``UserProfileOut`` is the enriched-principal shape the auth service's
``GET /v1/auth/me`` returns: profile fields from ``user_profiles`` plus the
coarse role names (supplied by the caller from the verified token's claims).
Clients identify users by ``id`` — the profile id. The Cognito subject never
crosses the wire: it is a ``kernel.identifiers`` mapping,
internal to resolution. The wire shape is unchanged by the language
normalisation: ``languages`` stays a list of ISO 639-1 codes, now derived from
the kernel's ``user_languages`` link rows (the ORM property
``UserProfile.language_codes`` — load the relationship before validating).

Extended additively: the profile page
needs the raw editable fields, so the wire gains ``first_name``/``last_name``,
the raw ``display_name_override`` (the nullable presentation override, distinct
from the effective ``display_name`` which existing consumers keep reading), and
``preferred_language``. No field was removed or changed shape.
"""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class UserProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    email: str
    first_name: str
    last_name: str
    display_name: str = Field(
        description="The effective name: the override when set, else 'First Last'"
    )
    display_name_override: str | None = Field(
        default=None, description="The raw presentation override; null when unset"
    )
    timezone: str = Field(description="IANA timezone, e.g. 'Europe/London'")
    preferred_language: str | None = Field(
        default=None,
        description="Preferred ISO 639-1 code (a spoken language); null if unset",
    )
    languages: list[str] = Field(
        default=[],
        validation_alias=AliasChoices("languages", "language_codes"),
        description="Spoken ISO 639-1 codes (from the user_languages links)",
    )
    roles: list[str] = Field(default=[], description="Coarse role names, e.g. ['Admin', 'Coach']")
