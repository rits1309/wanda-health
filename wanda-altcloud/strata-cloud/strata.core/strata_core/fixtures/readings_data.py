"""The readings walkthrough dataset — device registrations for the demo cast.

The ``readings-demo`` profile registers SmartMeter devices to demo cast members so
the readings runbook works out of the box: the two example
payloads carry the placeholder device id ``SM5000-IB-xxxx`` (``xxxx`` = elided
characters) and are posted **verbatim**, so that literal string is instantiated
here as two demo devices — Morgan's scale and Morgan's blood-pressure cuff, one
mapping per catalogue type. The remaining registrations give other members
distinct-id devices so listings and ad-hoc demos have breadth.

Like every profile dataset this is deterministic and idempotent:
registrations converge on the active ``(type, external id)`` natural key with the
registration API's supersede-for-audit semantics — a drifted mapping is ended,
never deleted.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from strata_core.fixtures.factories import register_device

#: One fixed instant for every fixture registration — deterministic, and safely
#: before the example payloads' July 2026 timestamps.
REGISTERED_AT = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)

#: The worked example payloads' device id, kept verbatim.
EXAMPLE_DEVICE_ID = "SM5000-IB-xxxx"


@dataclass(frozen=True)
class DeviceRegistration:
    registration_id: str  # deterministic row id
    member: str  # historical cast id — resolved through the people map, never literally
    type_name: str
    external_id: str


DEVICE_REGISTRATIONS: tuple[DeviceRegistration, ...] = (
    # Morgan's scale and cuff — the placeholder id under each catalogue type,
    # so both example payloads attribute when posted unmodified.
    DeviceRegistration("devreg-m1-scale", "m-1", "SmartMeter Scale", EXAMPLE_DEVICE_ID),
    DeviceRegistration("devreg-m1-bp", "m-1", "SmartMeter Blood Pressure", EXAMPLE_DEVICE_ID),
    # Distinct-id devices across the rest of the member cast.
    DeviceRegistration("devreg-m5-scale", "m-5", "SmartMeter Scale", "SM5000-IB-1201"),
    DeviceRegistration("devreg-m4-bp", "m-4", "SmartMeter Blood Pressure", "SM5000-IB-1310"),
    DeviceRegistration("devreg-m2-scale", "m-2", "SmartMeter Scale", "SM5000-IB-1044"),
)


async def load_readings_data(session: AsyncSession, *, people: dict[str, str]) -> None:
    """Converge the demo device registrations; ``people`` maps cast ids to actual
    profile ids (a cast email first provisioned outside the fixtures keeps its
    real id — reference people through the map, never literally)."""
    for registration in DEVICE_REGISTRATIONS:
        await register_device(
            session,
            user_id=people[registration.member],
            type_name=registration.type_name,
            external_id=registration.external_id,
            registered_at=REGISTERED_AT,
            registration_id=registration.registration_id,
        )
