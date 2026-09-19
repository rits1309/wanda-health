"""The device readings domain: registration lookup, pipeline seams, canonical readings.

Added for Strata.Connect device readings Phase 1. People are the kernel's ``user_profiles``. Writer:
``strata.connect`` — see ``strata_core.ownership``. The ``user_external_ids``
lookup moved to ``kernel.identifiers`` at Identity Mapping (
resolved); registration writes go through the
``strata_identity.identifiers`` seam.
"""

from strata_core.domains.device_readings.pipeline import (
    MAX_RECEIVE_COUNT,
    QUARANTINE_STATES,
    READING_QUEUE_STATES,
    READING_TYPES,
    QuarantinedReading,
    RawReading,
    ReadingQueueEntry,
    ReadingsOutboxEvent,
)
from strata_core.domains.device_readings.reading import (
    BloodPressureReading,
    WeightReading,
)
from strata_core.domains.device_readings.registration import READING_TYPE_EXTERNAL_ID_TYPES

__all__ = [
    "MAX_RECEIVE_COUNT",
    "QUARANTINE_STATES",
    "READING_QUEUE_STATES",
    "READING_TYPES",
    "READING_TYPE_EXTERNAL_ID_TYPES",
    "BloodPressureReading",
    "QuarantinedReading",
    "RawReading",
    "ReadingQueueEntry",
    "ReadingsOutboxEvent",
    "WeightReading",
]
