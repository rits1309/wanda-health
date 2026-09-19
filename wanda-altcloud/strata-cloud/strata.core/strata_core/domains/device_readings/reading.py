"""Canonical readings: one typed table per reading type.

Both tables carry the same envelope: the resolved person (``user_id``), the
device, the provider's ``reading_id`` (globally unique per contract — the
provider dedup key), the pipeline ``correlation_id`` (unique — the
replay dedup key), and ``raw_ref`` tracing every row back to the stored
raw payload. Rows are append-only; out-of-range values are
flagged ``suspect``, never rejected. All timestamps are UTC.

Weight readings persist every unit system the device supplied, unaltered —
downstream conversions need documented formulas for auditors — so the
kg and lbs columns are each nullable and filled only when supplied.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id


class WeightReading(TimestampMixin, Base):
    __tablename__ = "weight_readings"
    __table_args__ = (
        # A canonical weight reading with no weight is a pipeline bug, and append-only
        # rows would preserve it silently — at least one unit system is present.
        CheckConstraint("weight_kg IS NOT NULL OR weight_lbs IS NOT NULL", name="has_measurement"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    provider_reading_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    tare_kg: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    weight_lbs: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    tare_lbs: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    suspect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlation_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    raw_ref: Mapped[str] = mapped_column(ForeignKey("raw_readings.id"), nullable=False)


class BloodPressureReading(TimestampMixin, Base):
    __tablename__ = "blood_pressure_readings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    provider_reading_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    systolic_mmhg: Mapped[int] = mapped_column(Integer, nullable=False)
    diastolic_mmhg: Mapped[int] = mapped_column(Integer, nullable=False)
    pulse_bpm: Mapped[int] = mapped_column(Integer, nullable=False)
    irregular: Mapped[bool] = mapped_column(Boolean, nullable=False)
    suspect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlation_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    raw_ref: Mapped[str] = mapped_column(ForeignKey("raw_readings.id"), nullable=False)
