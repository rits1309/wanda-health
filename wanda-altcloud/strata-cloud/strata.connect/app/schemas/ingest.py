"""API contracts for the ingest edge and the registration operation."""

from datetime import datetime

from pydantic import BaseModel, Field


class IngestAccepted(BaseModel):
    """202 body: the capture receipt. The correlation id is the replay/trace handle."""

    correlation_id: str = Field(description="Assigned at ingest; traces the reading everywhere.")


class RegistrationBody(BaseModel):
    """What the dispatch caller sends."""

    user_id: str = Field(max_length=64, description="The target Member's kernel profile id.")
    identifier_type: str = Field(
        max_length=128,
        description='Catalogue name of the identifier source, e.g. "SmartMeter Scale".',
    )
    external_id: str = Field(
        min_length=1, max_length=256, description="The device identifier being linked."
    )


class RegistrationOut(BaseModel):
    """201 body: the active mapping that now attributes readings."""

    id: str
    user_id: str
    identifier_type: str
    external_id: str
    registered_at: datetime
    superseded_user_id: str | None = Field(
        default=None,
        description="Set when an existing active mapping to another Member was ended (5.2b).",
    )
