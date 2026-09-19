"""Reminders, secure links, and suggested alternatives.

Tokens are stored hashed — the raw signed token only ever appears in the notification link.

Ported shape-for-shape from ``strata.booking``, except that the
people columns (``coach_id``/``member_id``) now reference the kernel's
``user_profiles`` — the local coach/member/admin stand-ins are gone;
role-ness is enforced by service-level guards, never DDL.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"), nullable=False)
    recipient_kind: Mapped[str] = mapped_column(String, nullable=False)  # member | coach
    recipient_id: Mapped[str] = mapped_column(String, nullable=False)
    due_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending"
    )  # pending|sent|cancelled
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RescheduleToken(TimestampMixin, Base):
    """Time-limited, appointment-scoped link token for one-click rescheduling."""

    __tablename__ = "reschedule_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    audience: Mapped[str] = mapped_column(String, nullable=False)  # member | coach
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SuggestedAlternative(TimestampMixin, Base):
    """One offered alternative after a coach cancellation, accept-or-reject only."""

    __tablename__ = "suggested_alternatives"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    cancelled_appointment_id: Mapped[str] = mapped_column(
        ForeignKey("appointments.id"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(
        String, nullable=False
    )  # same_time_other_coach | same_day_window
    slot_id: Mapped[str] = mapped_column(ForeignKey("slots.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # offered | accepted | rejected | expired | slot_taken
    status: Mapped[str] = mapped_column(String, nullable=False, default="offered")
