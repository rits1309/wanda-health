"""The booking domain: appointments, availability, engagement, platform, policies.

Ported from ``strata.booking``. People are the kernel's
``user_profiles`` (booking's local ``coaches``/``members``/``admins`` stand-ins
are gone); programmes and their assignments are the kernel's.
Writer: ``strata.booking`` — see ``strata_core.ownership``.
"""

from strata_core.domains.booking.appointment import (
    Appointment,
    ReassignmentEvent,
    RescheduleEvent,
)
from strata_core.domains.booking.availability import (
    AvailabilityPattern,
    AvailabilityPatternVersion,
    Slot,
    UnavailabilityBlock,
    UnavailabilityPattern,
)
from strata_core.domains.booking.engagement import (
    Reminder,
    RescheduleToken,
    SuggestedAlternative,
)
from strata_core.domains.booking.platform import CallRecord, Notification, OutboxEvent
from strata_core.domains.booking.policy import CancellationPolicy

__all__ = [
    "Appointment",
    "AvailabilityPattern",
    "AvailabilityPatternVersion",
    "CallRecord",
    "CancellationPolicy",
    "Notification",
    "OutboxEvent",
    "ReassignmentEvent",
    "Reminder",
    "RescheduleEvent",
    "RescheduleToken",
    "Slot",
    "SuggestedAlternative",
    "UnavailabilityBlock",
    "UnavailabilityPattern",
]
