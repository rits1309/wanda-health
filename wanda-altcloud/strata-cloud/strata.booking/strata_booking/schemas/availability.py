"""Pydantic contracts for availability patterns, slots, and the impact dry-run."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


class PatternBody(BaseModel):
    """Create/replace body for a recurring availability pattern (times are coach-local)."""

    days_of_week: list[int] = Field(min_length=1, description="0=Monday … 6=Sunday")
    start_time_local: time
    end_time_local: time
    slot_duration_minutes: int = Field(ge=10, le=480)
    timezone: str = Field(description="IANA timezone, e.g. America/New_York")
    active_from: date
    active_to: date

    @field_validator("days_of_week")
    @classmethod
    def _valid_days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days_of_week entries must be 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    @field_validator("start_time_local", "end_time_local")
    @classmethod
    def _naive_wall_clock(cls, v: time) -> time:
        if v.tzinfo is not None:
            raise ValueError("times are coach-local wall-clock values; do not include an offset")
        return v

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {v!r}") from exc
        return v

    @model_validator(mode="after")
    def _valid_ranges(self) -> "PatternBody":
        if self.start_time_local >= self.end_time_local:
            raise ValueError("start_time_local must be before end_time_local")
        if self.active_from > self.active_to:
            raise ValueError("active_from must not be after active_to")
        return self


class PatternOut(BaseModel):
    id: str
    coach_id: str
    days_of_week: list[int]
    # Wall-clock local times ("HH:MM:SS", no offset) — deliberately plain strings, because the
    # JSON-Schema "time" format implies an RFC 3339 offset these values must not carry.
    start_time_local: str
    end_time_local: str
    slot_duration_minutes: int
    timezone: str
    active_from: date
    active_to: date
    version: int
    status: str


class ImpactedAppointment(BaseModel):
    """A booked appointment that a proposed pattern change/delete would cancel."""

    appointment_id: str
    member_id: str
    # Kernel display name, resolved server-side.
    member_name: str
    slot_start_utc: datetime
    slot_end_utc: datetime


class PatternImpact(BaseModel):
    pattern_id: str
    cancelled_appointments: list[ImpactedAppointment]


class SlotOut(BaseModel):
    id: str
    coach_id: str
    start_utc: datetime
    end_utc: datetime
    duration_minutes: int
    status: str
