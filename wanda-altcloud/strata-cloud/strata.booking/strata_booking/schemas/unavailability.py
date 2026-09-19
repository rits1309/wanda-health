"""Pydantic contracts for unavailability and the coach calendar layers."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


class _LocalWindow(BaseModel):
    start_time_local: time
    end_time_local: time
    timezone: str = Field(description="IANA timezone, e.g. America/New_York")

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
    def _ordered(self) -> "_LocalWindow":
        if self.start_time_local >= self.end_time_local:
            raise ValueError("start_time_local must be before end_time_local")
        return self


class BlockBody(_LocalWindow):
    """One-off unavailable time on a specific date."""

    date: date


class BlockOut(BaseModel):
    id: str
    coach_id: str
    date: date
    start_time_local: str
    end_time_local: str
    timezone: str


class RecurringBody(_LocalWindow):
    """Recurring unavailable time, shaped like an availability pattern."""

    days_of_week: list[int] = Field(min_length=1, description="0=Monday … 6=Sunday")
    active_from: date
    active_to: date

    @field_validator("days_of_week")
    @classmethod
    def _valid_days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days_of_week entries must be 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    @model_validator(mode="after")
    def _active_ordered(self) -> "RecurringBody":
        if self.active_from > self.active_to:
            raise ValueError("active_from must not be after active_to")
        return self


class RecurringOut(BaseModel):
    id: str
    coach_id: str
    days_of_week: list[int]
    start_time_local: str
    end_time_local: str
    timezone: str
    active_from: date
    active_to: date


class CalendarAppointment(BaseModel):
    id: str
    member_id: str
    # Kernel display name, resolved server-side.
    member_name: str
    status: str


class CalendarEntry(BaseModel):
    """One slot in the coach/admin calendar, with its live appointment when booked."""

    slot_id: str
    coach_id: str
    start_utc: datetime
    end_utc: datetime
    duration_minutes: int
    status: str  # available | booked | unavailable | cancelled
    appointment: CalendarAppointment | None = None


class CoachCalendar(BaseModel):
    coach_id: str
    # Kernel display name — the admin surfaces render
    # coaches by name, never by raw identifier.
    coach_name: str
    from_date: date
    to_date: date
    entries: list[CalendarEntry]


class AdminCalendar(BaseModel):
    """Combined multi-coach calendar with per-layer toggles."""

    from_date: date
    to_date: date
    layers: list[str]
    coaches: list[CoachCalendar]
