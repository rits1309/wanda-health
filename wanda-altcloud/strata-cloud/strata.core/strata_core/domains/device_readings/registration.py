"""Device-registration vocabulary.

The external-identifier TABLES live in the kernel — ``kernel.identifiers``
(moved at Identity, consumers repointed at): import
``UserExternalId`` / ``UserExternalIdType`` / ``EXTERNAL_ID_TYPE_IDS`` from
``strata_core.domains.kernel``, and write mappings only through the
``strata_identity.identifiers`` seam. This module owns what genuinely
belongs to readings: which identifier type resolves each reading type.
"""

from typing import Final

#: Which identifier type resolves each reading type (``reading_type``
#: selects the type, then (type, device_id) finds the active mapping). Devices are
#: single-type so the pairing is one-to-one — owned here so consumers
#: never hard-code it.
READING_TYPE_EXTERNAL_ID_TYPES: Final[dict[str, str]] = {
    "weight": "SmartMeter Scale",
    "blood_pressure": "SmartMeter Blood Pressure",
}
