"""Plausibility screening: flag as suspect, never reject.

The shipped bounds are *provisional engineering bounds* — deliberately
generous, catching only the physically implausible (the example: an 8 kg weight from
an adult programme member) — pending clinical ranges from the incoming CMO (a
follow-up). The Worker persists out-of-bounds readings marked ``suspect``; nothing is
dropped or altered here.

Tare values are unbounded: a tare of 0 is normal and a wrong tare doesn't make the reading
implausible on its own. Only measurement fields are screened.
"""

from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.readings import CanonicalReading, CanonicalWeightReading


class Range(BaseModel):
    """An inclusive plausibility interval."""

    model_config = ConfigDict(frozen=True)

    lower: Decimal
    upper: Decimal

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        # An inverted interval excludes every value — a config typo (swapped arguments)
        # would silently mark all readings suspect; fail loudly at config time instead.
        if self.lower > self.upper:
            raise ValueError("range lower bound exceeds upper bound")
        return self

    def excludes(self, value: Decimal | int | None) -> bool:
        """True when a supplied value falls outside the interval; absent values pass."""
        return value is not None and not self.lower <= value <= self.upper


class PlausibilityBounds(BaseModel):
    """The bounds config. Defaults are the provisional engineering values."""

    model_config = ConfigDict(frozen=True)

    weight_kg: Range = Range(lower=Decimal(10), upper=Decimal(500))
    weight_lbs: Range = Range(lower=Decimal(22), upper=Decimal(1100))
    systolic_mmhg: Range = Range(lower=Decimal(50), upper=Decimal(300))
    diastolic_mmhg: Range = Range(lower=Decimal(30), upper=Decimal(200))
    pulse_bpm: Range = Range(lower=Decimal(20), upper=Decimal(250))


DEFAULT_BOUNDS = PlausibilityBounds()


def implausible_fields(
    reading: CanonicalReading, bounds: PlausibilityBounds = DEFAULT_BOUNDS
) -> tuple[str, ...]:
    """Names of the reading's measurement fields that fall outside plausibility bounds.

    Returns field *names* only, never values — safe to log and to persist on quarantine or
    audit records (no clinical values in logs).
    """
    if isinstance(reading, CanonicalWeightReading):
        checks: tuple[tuple[str, Decimal | int | None, Range], ...] = (
            ("weight_kg", reading.weight_kg, bounds.weight_kg),
            ("weight_lbs", reading.weight_lbs, bounds.weight_lbs),
        )
    else:
        checks = (
            ("systolic_mmhg", reading.systolic_mmhg, bounds.systolic_mmhg),
            ("diastolic_mmhg", reading.diastolic_mmhg, bounds.diastolic_mmhg),
            ("pulse_bpm", reading.pulse_bpm, bounds.pulse_bpm),
        )
    return tuple(name for name, value, valid in checks if valid.excludes(value))


def is_suspect(reading: CanonicalReading, bounds: PlausibilityBounds = DEFAULT_BOUNDS) -> bool:
    """The suspect flag the Worker persists: any measurement out of bounds."""
    return bool(implausible_fields(reading, bounds))
