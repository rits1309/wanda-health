"""Canonical schema validation."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import get_args

import pytest
from pydantic import ValidationError
from strata_core.domains.device_readings import READING_TYPES

from app.schemas import CanonicalBloodPressureReading, CanonicalWeightReading, parse_reading
from tests.sample_payloads import BP_PAYLOAD, WEIGHT_PAYLOAD


def test_weight_example_canonicalised() -> None:
    """the worked weight payload parses; both unit systems kept."""
    reading = parse_reading(WEIGHT_PAYLOAD)

    assert isinstance(reading, CanonicalWeightReading)
    assert reading.reading_id == 12346
    assert reading.weight_kg == Decimal("81.6")
    assert reading.tare_kg == Decimal("0.0")
    assert reading.weight_lbs == Decimal("180.0")
    assert reading.tare_lbs == Decimal("0.0")


def test_bp_example_canonicalised() -> None:
    """The worked BP payload canonicalises with its vitals intact."""
    reading = parse_reading(BP_PAYLOAD)

    assert isinstance(reading, CanonicalBloodPressureReading)
    assert reading.systolic_mmhg == 128
    assert reading.diastolic_mmhg == 82
    assert reading.pulse_bpm == 71
    assert reading.irregular is False


def test_naive_timestamps_treated_as_utc() -> None:
    """/ the samples' naive timestamps come out UTC-aware, wall-time unchanged."""
    reading = parse_reading(BP_PAYLOAD)

    assert reading.date_recorded == datetime(2026, 7, 1, 14, 32, tzinfo=UTC)
    assert reading.date_received == datetime(2026, 7, 1, 14, 33, 10, tzinfo=UTC)


def test_aware_timestamps_converted_to_utc() -> None:
    """Offset-aware timestamps are converted to UTC on parse."""
    payload = {**BP_PAYLOAD, "date_recorded": "2026-07-01T15:32:00+01:00"}

    reading = parse_reading(payload)

    assert reading.date_recorded == datetime(2026, 7, 1, 14, 32, tzinfo=UTC)
    assert reading.date_recorded.tzinfo == UTC


def test_unknown_reading_type_rejected() -> None:
    """an unrecognised reading_type fails validation."""
    with pytest.raises(ValidationError):
        parse_reading({**WEIGHT_PAYLOAD, "reading_type": "spo2"})


def test_missing_field_rejected() -> None:
    """A payload missing a required field fails validation."""
    payload = dict(BP_PAYLOAD)
    del payload["systolic_mmhg"]

    with pytest.raises(ValidationError):
        parse_reading(payload)


def test_wrong_type_rejected() -> None:
    """A payload with a wrongly typed field fails validation."""
    with pytest.raises(ValidationError):
        parse_reading({**BP_PAYLOAD, "systolic_mmhg": "high"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("systolic_mmhg", True),  # bool is a subclass of int — must not become 1 mmHg
        ("irregular", 1),  # ints must not become the irregular-rhythm flag
        ("date_recorded", 1751379120),  # bare numbers are not ISO-8601 timestamps
        ("reading_id", "12345"),  # the dedup key stays consistently typed
    ],
)
def test_lax_coercions_rejected(field: str, value: object) -> None:
    """wrong JSON types fail validation rather than coercing silently."""
    with pytest.raises(ValidationError):
        parse_reading({**BP_PAYLOAD, field: value})


def test_single_unit_system_accepted() -> None:
    """a device supplying only pounds is stored as supplied — kg stays absent."""
    payload = dict(WEIGHT_PAYLOAD)
    del payload["weight_kg"]
    del payload["tare_kg"]

    reading = parse_reading(payload)

    assert isinstance(reading, CanonicalWeightReading)
    assert reading.weight_kg is None
    assert reading.weight_lbs == Decimal("180.0")


def test_weight_with_no_measurement_rejected() -> None:
    """Mirrors strata-core's ck_weight_readings_has_measurement."""
    payload = dict(WEIGHT_PAYLOAD)
    del payload["weight_kg"]
    del payload["weight_lbs"]

    with pytest.raises(ValidationError):
        parse_reading(payload)


def test_unknown_extra_fields_ignored() -> None:
    """Contract tolerance: provider additions don't break ingest; the raw payload keeps them."""
    reading = parse_reading({**BP_PAYLOAD, "firmware_version": "2.1.0"})

    assert isinstance(reading, CanonicalBloodPressureReading)


def test_discriminator_matches_kernel_vocabulary() -> None:
    """Every reading_type literal in the union is exactly strata-core's READING_TYPES."""
    literals = {
        arg
        for model in (CanonicalWeightReading, CanonicalBloodPressureReading)
        for arg in get_args(model.model_fields["reading_type"].annotation)
    }
    assert literals == set(READING_TYPES)
