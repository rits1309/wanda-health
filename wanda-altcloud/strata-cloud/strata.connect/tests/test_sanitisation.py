"""Plausibility screening (; provisional bounds)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import parse_reading
from app.services.sanitisation import (
    PlausibilityBounds,
    Range,
    implausible_fields,
    is_suspect,
)
from tests.sample_payloads import BP_PAYLOAD, WEIGHT_PAYLOAD


def test_worked_examples_are_plausible() -> None:
    """The worked weight and BP examples pass the plausibility screen."""
    assert not is_suspect(parse_reading(WEIGHT_PAYLOAD))
    assert not is_suspect(parse_reading(BP_PAYLOAD))


def test_implausible_weight_flagged() -> None:
    """The example: an 8 kg reading from an adult programme member."""
    reading = parse_reading({**WEIGHT_PAYLOAD, "weight_kg": 8, "weight_lbs": 17.6})

    assert is_suspect(reading)
    assert implausible_fields(reading) == ("weight_kg", "weight_lbs")


def test_implausible_bp_flagged_by_field_name_only() -> None:
    """the report names fields, never values — safe for logs."""
    reading = parse_reading({**BP_PAYLOAD, "systolic_mmhg": 400, "pulse_bpm": 10})

    assert implausible_fields(reading) == ("systolic_mmhg", "pulse_bpm")


def test_bounds_are_inclusive() -> None:
    """Plausibility bounds are inclusive: a reading exactly on the bound is not suspect."""
    reading = parse_reading({**WEIGHT_PAYLOAD, "weight_kg": 10, "weight_lbs": 22})

    assert not is_suspect(reading)


def test_absent_unit_system_not_screened() -> None:
    """Only supplied values are screened; an absent unit system can't be out of bounds."""
    payload = dict(WEIGHT_PAYLOAD)
    del payload["weight_kg"]
    del payload["tare_kg"]

    assert not is_suspect(parse_reading(payload))


def test_bounds_are_configurable() -> None:
    """the CMO's clinical ranges arrive as config, not a code change."""
    clinical = PlausibilityBounds(weight_kg=Range(lower=Decimal(30), upper=Decimal(250)))

    reading = parse_reading(WEIGHT_PAYLOAD)
    assert not is_suspect(reading, clinical)
    assert is_suspect(parse_reading({**WEIGHT_PAYLOAD, "weight_kg": 25}), clinical)


def test_inverted_range_rejected() -> None:
    """A swapped-arguments config typo fails at config time, not as all-readings-suspect."""
    with pytest.raises(ValidationError):
        Range(lower=Decimal(250), upper=Decimal(30))
