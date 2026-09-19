"""Canonical reading schemas.

A discriminated union on ``reading_type`` over the common envelope: every inbound
SmartMeter payload either validates into exactly one canonical form here or fails
validation and is routed to retry/quarantine handling — the raw payload
itself is always preserved verbatim upstream, so canonicalisation never loses data.

Unknown fields the provider may add are ignored, not rejected: the inbound contract is
unconfirmed until Phase 2, and the raw payload already preserves everything.
Unknown ``reading_type`` values, missing fields, and wrong types still fail validation —
strict types on the clinical fields, so e.g. a boolean is never coerced into a pressure.

Named ``Canonical*`` (not the plan's shorthand ``WeightReading``) because the kernel's ORM
models in ``strata_core.domains.device_readings`` already carry those names, and the
processor/worker will import both representations side by side.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    field_validator,
    model_validator,
)


class ReadingEnvelope(BaseModel):
    """Fields common to every SmartMeter payload."""

    # Frozen: readings are append-only clinical facts; nothing downstream mutates them.
    model_config = ConfigDict(frozen=True)

    reading_id: StrictInt = Field(description="Provider's reading id — globally unique.")
    device_id: str = Field(description="Device serial the reading came from.")
    device_model: str = Field(description="Provider's device model code, e.g. SM5000-IB.")
    date_recorded: datetime = Field(description="When the member took the reading (UTC).")
    date_received: datetime = Field(description="When the provider received it (UTC).")

    @field_validator("date_recorded", "date_received", mode="before")
    @classmethod
    def _reject_numeric_timestamps(cls, value: object) -> object:
        # The contract's timestamps are ISO-8601 strings; a bare number is a wrong type,
        # not an epoch to guess an interpretation for.
        if isinstance(value, int | float):
            raise ValueError("timestamps must be ISO-8601 strings, not numbers")
        return value

    @field_validator("date_recorded", "date_received")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        # Naive timestamps are treated as UTC (to be confirmed against the
        # SmartMeter contract in Phase 2); aware ones are converted.
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class CanonicalWeightReading(ReadingEnvelope):
    """A scale reading. Values stay in every unit system the device supplied.

    Each unit system is optional as a pair, but a reading with no weight in any system is
    meaningless — rejected here, mirroring ``ck_weight_readings_has_measurement`` in strata-core.
    """

    reading_type: Literal["weight"]
    weight_kg: Decimal | None = Field(default=None, description="Gross weight in kilograms.")
    tare_kg: Decimal | None = Field(default=None, description="Tare in kilograms.")
    weight_lbs: Decimal | None = Field(default=None, description="Gross weight in pounds.")
    tare_lbs: Decimal | None = Field(default=None, description="Tare in pounds.")

    @model_validator(mode="after")
    def _has_measurement(self) -> Self:
        if self.weight_kg is None and self.weight_lbs is None:
            raise ValueError("a weight reading must carry weight_kg or weight_lbs")
        return self


class CanonicalBloodPressureReading(ReadingEnvelope):
    """A blood-pressure cuff reading."""

    reading_type: Literal["blood_pressure"]
    systolic_mmhg: StrictInt = Field(description="Systolic pressure, mmHg.")
    diastolic_mmhg: StrictInt = Field(description="Diastolic pressure, mmHg.")
    pulse_bpm: StrictInt = Field(description="Pulse, beats per minute.")
    irregular: StrictBool = Field(description="Device's irregular-rhythm indication.")


CanonicalReading = Annotated[
    CanonicalWeightReading | CanonicalBloodPressureReading, Field(discriminator="reading_type")
]
"""One validated reading of either type; ``reading_type`` selects the model."""

_READING_ADAPTER: TypeAdapter[CanonicalReading] = TypeAdapter(CanonicalReading)


def parse_reading(payload: Any) -> CanonicalReading:
    """Validate a decoded payload (e.g. the RawStore's JSONB dict) into canonical form.

    Raises ``pydantic.ValidationError`` on unknown ``reading_type``, missing fields, or
    wrong types — the caller routes that to retry/dead-letter, never a drop.
    """
    return _READING_ADAPTER.validate_python(payload)
