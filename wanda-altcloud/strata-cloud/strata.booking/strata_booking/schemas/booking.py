"""Pydantic contracts for slot browsing and appointments."""

from datetime import datetime

from pydantic import BaseModel


class BrowseSlot(BaseModel):
    """An available slot as a member sees it (times UTC; client renders member-local)."""

    slot_id: str
    coach_id: str
    coach_name: str
    coach_languages: list[str]
    start_utc: datetime
    end_utc: datetime
    duration_minutes: int


class BookingBody(BaseModel):
    slot_id: str
    # Required when a coach/admin books on behalf of a member; forbidden for member bookings.
    member_id: str | None = None
    # The member's "match my preferred language" toggle state at booking time — snapshotted on
    # the appointment so reassignment can honour the original criteria.
    language_matched: bool = False


class AppointmentOut(BaseModel):
    id: str
    slot_id: str
    coach_id: str
    member_id: str
    # Kernel display name, resolved server-side — clients never
    # look identities up themselves.
    member_name: str
    programme_id: str
    status: str
    created_by: str
    start_utc: datetime
    end_utc: datetime
    booking_language_matched: bool
    original_appointment_id: str | None = None


class RescheduleBody(BaseModel):
    slot_id: str


class RescheduleRequestOut(BaseModel):
    """Confirmation that the member's reschedule link was sent."""

    appointment_id: str
    expires_at: datetime


class RescheduleEventOut(BaseModel):
    from_appointment_id: str
    to_appointment_id: str
    initiated_by: str
    from_slot_id: str
    to_slot_id: str
    occurred_at: datetime


class AppointmentHistoryOut(BaseModel):
    """The full reschedule chain, oldest record first."""

    appointments: list[AppointmentOut]
    reschedules: list[RescheduleEventOut]


class RescheduleLinkOut(BaseModel):
    """The tokened reschedule page payload: the appointment + same-coach bookable slots."""

    appointment: AppointmentOut
    slots: list[BrowseSlot]


class ReassignmentOption(BaseModel):
    """Another coach's slot at the exact original start/duration."""

    slot_id: str
    coach_id: str
    coach_name: str
    coach_languages: list[str]


class ReassignBody(BaseModel):
    slot_id: str
