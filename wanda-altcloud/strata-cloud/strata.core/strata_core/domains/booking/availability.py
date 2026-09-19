"""Availability, unavailability, and the generated slots.

Pattern times are local to the coach (time-of-day + IANA timezone); each generated occurrence
is converted to UTC individually, so DST discontinuity is accepted per the MVP note.
Slots are generated lazily to a rolling horizon; ``generation_watermark`` records how far a
pattern has been materialised.

Ported shape-for-shape from ``strata.booking``, except that the
people columns (``coach_id``/``member_id``) now reference the kernel's
``user_profiles`` — the local coach/member/admin stand-ins are gone;
role-ness is enforced by service-level guards, never DDL.
"""

from datetime import date, datetime, time

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id


class AvailabilityPattern(TimestampMixin, Base):
    __tablename__ = "availability_patterns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    coach_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)  # 0=Mon..6=Sun
    start_time_local: Mapped[time] = mapped_column(Time, nullable=False)
    end_time_local: Mapped[time] = mapped_column(Time, nullable=False)
    slot_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    active_from: Mapped[date] = mapped_column(Date, nullable=False)
    active_to: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")  # active|deleted
    generation_watermark: Mapped[date | None] = mapped_column(Date, nullable=True)


class AvailabilityPatternVersion(TimestampMixin, Base):
    """Audit snapshot of every pattern version (updates are versioned)."""

    __tablename__ = "availability_pattern_versions"
    __table_args__ = (UniqueConstraint("pattern_id", "version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    pattern_id: Mapped[str] = mapped_column(ForeignKey("availability_patterns.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[str] = mapped_column(String, nullable=False)
    on_behalf_of_coach_id: Mapped[str | None] = mapped_column(String, nullable=True)


class UnavailabilityBlock(TimestampMixin, Base):
    """One-off unavailable time on a specific date."""

    __tablename__ = "unavailability_blocks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    coach_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time_local: Mapped[time] = mapped_column(Time, nullable=False)
    end_time_local: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    on_behalf_of_coach_id: Mapped[str | None] = mapped_column(String, nullable=True)


class UnavailabilityPattern(TimestampMixin, Base):
    """Recurring unavailable time, shaped like an availability pattern."""

    __tablename__ = "unavailability_patterns"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    coach_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    start_time_local: Mapped[time] = mapped_column(Time, nullable=False)
    end_time_local: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    active_from: Mapped[date] = mapped_column(Date, nullable=False)
    active_to: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")  # active|deleted
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    on_behalf_of_coach_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Slot(TimestampMixin, Base):
    __tablename__ = "slots"
    __table_args__ = (UniqueConstraint("coach_id", "start_utc", "end_utc"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    coach_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    pattern_id: Mapped[str | None] = mapped_column(
        ForeignKey("availability_patterns.id"), nullable=True
    )
    pattern_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # available | booked | unavailable | cancelled
    status: Mapped[str] = mapped_column(String, nullable=False, default="available")
