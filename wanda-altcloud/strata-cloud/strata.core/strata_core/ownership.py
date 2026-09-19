"""The ownership registry — a single writing service per domain.

Any service may read any canonical table; only the owning service writes to a
domain's tables, and cross-service writes go through the owning service's API.
``OWNERSHIP.md`` is the human-readable half; the test suite asserts this map
covers every table registered on the canonical metadata, which is what makes
ownership mechanically checkable.
"""

from typing import Final

#: Domain module → the one service that writes it.
DOMAIN_WRITERS: Final[dict[str, str]] = {
    "kernel.people": "strata.engine.auth",  # profile provisioning + role assignment
    "kernel.programmes": "strata.booking",  # programme setup + membership
    # The identity write path: every mapping write — Cognito adoption,
    # device registration, the legacy import — goes through strata.engine.auth's owned
    # seam code; Strata.Connect's registration writes route through it (Identity).
    "kernel.identifiers": "strata.engine.auth",
    "reference": "strata.terminology",  # ingest pipelines (extracted from strata.engine)
    "booking": "strata.booking",  # the booking service's domain (from)
    "device_readings": "strata.connect",  # device readings pipeline (from Connect)
    # The member-profile service does not exist yet — the domain lands
    # ahead of it (option-5 separability); canonical name is (Member, never
    # Patient). Writes go through the `strata.member` seam library:
    # until the service ships, only fixtures + the import (via that seam)
    # write here. The registry names the owning service, not the library.
    "clinical": "member-profile service (future)",
}

#: Canonical table → domain module.
TABLE_DOMAINS: Final[dict[str, str]] = {
    "user_profiles": "kernel.people",
    "roles": "kernel.people",
    "user_roles": "kernel.people",
    "languages": "kernel.people",
    "user_languages": "kernel.people",
    "programmes": "kernel.programmes",
    "programme_assignments": "kernel.programmes",
    "diagnoses": "reference",
    "procedures": "reference",
    "products": "reference",
    "active_ingredients": "reference",
    "packagings": "reference",
    "product_identifiers": "reference",
    "appointments": "booking",
    "reschedule_events": "booking",
    "reassignment_events": "booking",
    "availability_patterns": "booking",
    "availability_pattern_versions": "booking",
    "unavailability_blocks": "booking",
    "unavailability_patterns": "booking",
    "slots": "booking",
    "reminders": "booking",
    "reschedule_tokens": "booking",
    "suggested_alternatives": "booking",
    "outbox_events": "booking",
    "notifications": "booking",
    "call_records": "booking",
    "cancellation_policies": "booking",
    "member_diagnoses": "clinical",
    "member_procedures": "clinical",
    "member_medications": "clinical",
    "member_demographics": "clinical",
    "user_external_id_types": "kernel.identifiers",
    "user_external_ids": "kernel.identifiers",
    "raw_readings": "device_readings",
    "reading_queue": "device_readings",
    "weight_readings": "device_readings",
    "blood_pressure_readings": "device_readings",
    "quarantined_readings": "device_readings",
    "readings_outbox_events": "device_readings",
}


def writer_for(table_name: str) -> str:
    """The service allowed to write ``table_name`` (KeyError = not canonical)."""
    return DOMAIN_WRITERS[TABLE_DOMAINS[table_name]]
