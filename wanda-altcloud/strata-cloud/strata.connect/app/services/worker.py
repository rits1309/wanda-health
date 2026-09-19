"""The Worker: sanitise, dedup, persist + outbox.

The last pipeline stage. Runs inside the consumer's processing transaction, so the
reading row, its outbox event, and the queue ack commit **atomically** (
the platform's dual-output pattern, shape). The event's ``published_at`` stays
NULL: the relay to Elevate is gated on, both still Open — capture the
business event durably, publish nothing.

Dedup is select-first on BOTH keys (the booking idempotency-key precedent): an existing
row for the correlation id (queue redelivery, replay) or the provider's reading id
(the provider re-sending under a fresh delivery) means the reading is already in
Strata.Core — acknowledge and write nothing. A concurrent-delivery race
on those unique keys is caught at a savepoint and treated as the same outcome.

the Processor→Worker handoff stays an in-process call behind this Protocol; a queue
can be inserted later without changing either side's contract.
"""

from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.device_readings import (
    BloodPressureReading,
    ReadingsOutboxEvent,
    WeightReading,
)

from app.clock import clock
from app.schemas.readings import CanonicalWeightReading
from app.services.processor import Attributed
from app.services.sanitisation import is_suspect

_log = structlog.get_logger(__name__)

#: The business event captured with every persisted reading.
READING_RECEIVED_EVENT = "reading.received"


class Worker(Protocol):
    """What the consumer hands an attributed reading to (contract)."""

    async def handle(self, session: AsyncSession, attributed: Attributed) -> None: ...


class PersistingWorker:
    """flag suspects, persist as supplied (17), event alongside."""

    async def handle(self, session: AsyncSession, attributed: Attributed) -> None:
        reading = attributed.reading
        # The canonical schema carries the provider id as StrictInt; the column is String
        # (the seam note) — stringified exactly once, here.
        provider_reading_id = str(reading.reading_id)
        model: type[WeightReading | BloodPressureReading] = (
            WeightReading if isinstance(reading, CanonicalWeightReading) else BloodPressureReading
        )

        already = (
            await session.execute(
                select(model.id).where(
                    (model.correlation_id == attributed.correlation_id)
                    | (model.provider_reading_id == provider_reading_id)
                )
            )
        ).first()
        if already is not None:
            # no new record, no new event; the caller acks as handled.
            _log.info("duplicate reading, nothing written", reading_row_id=already[0])
            return

        suspect = is_suspect(reading)
        if isinstance(reading, CanonicalWeightReading):
            row: WeightReading | BloodPressureReading = WeightReading(
                user_id=attributed.user_id,
                device_id=reading.device_id,
                provider_reading_id=provider_reading_id,
                recorded_at=reading.date_recorded,
                received_at=reading.date_received,
                weight_kg=reading.weight_kg,
                tare_kg=reading.tare_kg,
                weight_lbs=reading.weight_lbs,
                tare_lbs=reading.tare_lbs,
                suspect=suspect,
                correlation_id=attributed.correlation_id,
                raw_ref=attributed.raw_ref,
            )
        else:
            row = BloodPressureReading(
                user_id=attributed.user_id,
                device_id=reading.device_id,
                provider_reading_id=provider_reading_id,
                recorded_at=reading.date_recorded,
                received_at=reading.date_received,
                systolic_mmhg=reading.systolic_mmhg,
                diastolic_mmhg=reading.diastolic_mmhg,
                pulse_bpm=reading.pulse_bpm,
                irregular=reading.irregular,
                suspect=suspect,
                correlation_id=attributed.correlation_id,
                raw_ref=attributed.raw_ref,
            )

        try:
            # Savepoint: a concurrent delivery may win the unique-key race between our
            # select and this insert — that is the duplicate outcome, not a fault.
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError as exc:  # pragma: no cover — only the concurrent race reaches here
            # Constraint names carry the column name (the kernel's NAMING_CONVENTION). A
            # miss mis-classifies the duplicate as a fault, which self-heals: the retry's
            # select-first dedup acks it. Parameters are hidden engine-wide.
            if "correlation_id" in str(exc.orig) or "provider_reading_id" in str(exc.orig):
                _log.info("duplicate reading (concurrent delivery), nothing written")
                return
            raise

        session.add(
            ReadingsOutboxEvent(
                event_type=READING_RECEIVED_EVENT,
                # Identifiers and flags ONLY — no measurement values and no measurement
                # timestamps; the reading row is the data, the event points at it. The
                # relay's payload contract binds with.
                payload={
                    "reading_row_id": row.id,
                    "reading_type": reading.reading_type,
                    "user_id": attributed.user_id,
                    "correlation_id": attributed.correlation_id,
                    "suspect": suspect,
                },
                occurred_at=clock.now(),
            )
        )
        await session.flush()
        # Identifiers and the flag only — never measurement values.
        _log.info(
            "reading persisted",
            reading_row_id=row.id,
            reading_type=reading.reading_type,
            suspect=suspect,
        )


_worker = PersistingWorker()


def get_worker() -> Worker:
    """Dependency/wiring point — callers hold the seam, not the implementation."""
    return _worker
