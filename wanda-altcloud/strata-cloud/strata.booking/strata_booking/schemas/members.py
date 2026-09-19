"""Pydantic contract for the scoped members listing."""

from pydantic import BaseModel


class MemberOut(BaseModel):
    """A member as booking's staff surfaces see them.

    Exactly 's field set — the model IS the payload guard: no email, no
    further personal data on this listing.
    """

    id: str
    display_name: str
    preferred_language: str
    timezone: str
