"""Appointments, the reschedule chain, and reassignment audit.

Lifecycle states: confirmed → rescheduled | cancelled | no_show | completed;
rescheduled → cancelled | no_show | completed; the rest are terminal.

Reschedule-chain record semantics: each reschedule creates a **new** appointment record linked
via ``original_appointment_id``. The superseded record's status becomes
``rescheduled`` and is frozen (its slot is freed); the new record starts ``confirmed``. The
"rescheduled → …" transitions hold at the *chain* level — the latest record governs.

Every side-effecting write records the acting user, and — when an admin acts for a coach —
``on_behalf_of_coach_id``, so admin actions are distinguishable.

Ported shape-for-shape from ``strata.booking``, except that the
people columns (``coach_id``/``member_id``) now reference the kernel's
``user_profiles`` — the local coach/member/admin stand-ins are gone;
role-ness is enforced by service-level guards, never DDL.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id


class Appointment(TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        # One member per slot: at most one live (confirmed) appointment on any slot.
        Index(
            "uq_appointments_slot_confirmed",
            "slot_id",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
        ),
        # The calendar join reads confirmed | no_show | completed: terminal
        # rows sit outside the partial unique index above and accumulate forever
        # (never deleted), so the join needs plain (slot_id, status) coverage.
        Index("ix_appointments_slot_status", "slot_id", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    slot_id: Mapped[str] = mapped_column(ForeignKey("slots.id"), nullable=False)
    coach_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    member_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    programme_id: Mapped[str] = mapped_column(ForeignKey("programmes.id"), nullable=False)
    # confirmed | rescheduled | cancelled | no_show | completed
    status: Mapped[str] = mapped_column(String, nullable=False, default="confirmed")
    created_by: Mapped[str] = mapped_column(String, nullable=False)  # member | coach | admin
    acting_user_id: Mapped[str] = mapped_column(String, nullable=False)
    on_behalf_of_coach_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Booking-criteria snapshot, retained so reassignment applies the original conditions.
    booking_language_matched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    booking_preferred_language: Mapped[str | None] = mapped_column(String, nullable=True)
    original_appointment_id: Mapped[str | None] = mapped_column(
        ForeignKey("appointments.id"), nullable=True
    )
    cancelled_by: Mapped[str | None] = mapped_column(String, nullable=True)  # member|coach|system
    cancellation_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    no_show_detection: Mapped[str | None] = mapped_column(String, nullable=True)  # system | coach
    no_show_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)


class RescheduleEvent(TimestampMixin, Base):
    """One reschedule hop: from one appointment record to its successor."""

    __tablename__ = "reschedule_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    from_appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"), nullable=False)
    to_appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"), nullable=False)
    initiated_by: Mapped[str] = mapped_column(String, nullable=False)  # member | coach | admin
    acting_user_id: Mapped[str] = mapped_column(String, nullable=False)
    from_slot_id: Mapped[str] = mapped_column(ForeignKey("slots.id"), nullable=False)
    to_slot_id: Mapped[str] = mapped_column(ForeignKey("slots.id"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReassignmentEvent(TimestampMixin, Base):
    """Same-time coach handover audit: time and member unchanged."""

    __tablename__ = "reassignment_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"), nullable=False)
    from_coach_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    to_coach_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    from_slot_id: Mapped[str] = mapped_column(ForeignKey("slots.id"), nullable=False)
    to_slot_id: Mapped[str] = mapped_column(ForeignKey("slots.id"), nullable=False)
    initiating_user_id: Mapped[str] = mapped_column(String, nullable=False)
    on_behalf_of_coach_id: Mapped[str | None] = mapped_column(String, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
