"""The Processor: validate, resolve, quarantine.

Given a claimed queue entry: fetch the raw payload by its reference, validate it into
canonical form, and resolve the device to the Member holding the active
registration. Four outcomes, and every one is explicit — no failure path
silently drops a reading:

- **Dropped** — the correlation was DISCARDED by an operator (never-to-be-processed):
  ack and drop, logged. Checked first, so a replay enqueued before the discard cannot
  outlive it (the epic-gate finding) — the same guard ``operations.replay`` applies at
  enqueue time, enforced here where the message actually flows.
- **Attributed** — schema-valid and registered; handed to the Worker in-process.
- **Quarantined** — schema-valid but no active registration: a quarantine
  record pointing at the raw payload (never copying clinical values out of it),
  then ack. Redelivery before resolution finds the open record and creates no duplicate.
- **SchemaValidationFailure** (raised) — malformed payload: the caller
  records the error against the correlation id and fails the message into retry/DLQ.
"""

from dataclasses import dataclass

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.device_readings import (
    QUARANTINE_STATES,
    READING_TYPE_EXTERNAL_ID_TYPES,
    QuarantinedReading,
    ReadingQueueEntry,
)
from strata_core.domains.kernel import UserExternalId, UserExternalIdType

from app.schemas.readings import CanonicalReading, parse_reading
from app.services.raw_store import RawStore

_log = structlog.get_logger(__name__)

# The kernel owns the state vocabulary; the 3-way unpack keeps this arity-checked
# (discard is the operator's move — app/services/operations.py).
_QUARANTINE_OPEN, _QUARANTINE_RESOLVED, _QUARANTINE_DISCARDED = QUARANTINE_STATES


class SchemaValidationFailure(Exception):
    """the payload failed canonical validation; message goes to retry/DLQ."""

    def __init__(self, error: str) -> None:
        # Keep only the shape of the failure: pydantic messages carry input values,
        # and this string is recorded on the queue row and logged.
        super().__init__(error)


@dataclass(frozen=True)
class Attributed:
    """A canonical reading resolved to its Member — the Worker's input (handoff)."""

    reading: CanonicalReading
    user_id: str
    correlation_id: str
    raw_ref: str


@dataclass(frozen=True)
class Quarantined:
    """No active registration; the quarantine record exists (or already did) — ack."""

    quarantine_id: str


@dataclass(frozen=True)
class Dropped:
    """The correlation was discarded (never-to-be-processed) — ack and drop, logged."""

    quarantine_id: str


async def process(
    session: AsyncSession, store: RawStore, entry: ReadingQueueEntry
) -> Attributed | Quarantined | Dropped:
    """Run one claimed queue entry through validate → resolve; flushes, never commits."""
    # Discard is authoritative here too, before the payload is even fetched: a message
    # for a discarded correlation must never attribute NOR resurrect a worklist record.
    discarded = (
        await session.execute(
            select(QuarantinedReading.id).where(
                QuarantinedReading.correlation_id == entry.correlation_id,
                QuarantinedReading.state == _QUARANTINE_DISCARDED,
            )
        )
    ).scalar_one_or_none()
    if discarded is not None:
        return Dropped(discarded)

    payload = await store.get(session, entry.raw_ref)

    try:
        reading = parse_reading(payload)
    except ValidationError as exc:
        # Error shape only — locations and kinds, never the offending input values.
        shape = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['type']}" for e in exc.errors()
        )
        raise SchemaValidationFailure(f"canonical validation failed: {shape}") from exc

    mapping = await _active_mapping(session, reading)
    if mapping is None:
        return Quarantined(await _ensure_quarantined(session, entry, reading))

    # 's manual resolution flow closes here: a quarantined reading replayed after
    # its device was registered resolves its open record — atomically with the persist
    # and the ack, since all three share the consumer's processing transaction.
    open_record = await _open_record(session, entry.correlation_id)
    if open_record is not None:
        open_record.state = _QUARANTINE_RESOLVED
        _log.info("quarantine resolved", quarantine_id=open_record.id)

    return Attributed(
        reading=reading,
        user_id=mapping.user_id,
        correlation_id=entry.correlation_id,
        raw_ref=entry.raw_ref,
    )


async def _active_mapping(
    session: AsyncSession, reading: CanonicalReading
) -> UserExternalId | None:
    """reading_type selects the identifier type; (type, device_id) the mapping."""
    type_name = READING_TYPE_EXTERNAL_ID_TYPES.get(reading.reading_type)
    if type_name is None:
        # Vocabulary drift (a reading type with no identifier-type pairing) is a schema
        # problem, not a transient fault — same clean, sanitised path as.
        raise SchemaValidationFailure(
            f"no identifier-type mapping for reading type {reading.reading_type!r}"
        )
    return (
        await session.execute(
            select(UserExternalId)
            .join(UserExternalIdType, UserExternalId.type_id == UserExternalIdType.id)
            .where(
                UserExternalIdType.name == type_name,
                UserExternalId.external_id == reading.device_id,
                UserExternalId.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def _ensure_quarantined(
    session: AsyncSession, entry: ReadingQueueEntry, reading: CanonicalReading
) -> str:
    """Create the quarantine record, or return the open one a redelivery already made
    (the kernel's one-open-row-per-correlation index backs this — at this surface)."""
    existing = await _open_record(session, entry.correlation_id)
    if existing is not None:
        return existing.id

    record = QuarantinedReading(
        correlation_id=entry.correlation_id,
        raw_ref=entry.raw_ref,
        device_id=reading.device_id,
        reading_type=reading.reading_type,
    )
    try:
        # Savepoint: a concurrent delivery may win the one-open-row index race between
        # our select and this insert — that is success (the reading IS quarantined),
        # not a retryable fault.
        async with session.begin_nested():
            session.add(record)
    except IntegrityError:
        winner = await _open_record(session, entry.correlation_id)
        if winner is None:  # pragma: no cover — only the index race reaches here
            raise
        return winner.id
    return record.id


async def _open_record(session: AsyncSession, correlation_id: str) -> QuarantinedReading | None:
    return (
        await session.execute(
            select(QuarantinedReading).where(
                QuarantinedReading.correlation_id == correlation_id,
                QuarantinedReading.state == _QUARANTINE_OPEN,
            )
        )
    ).scalar_one_or_none()
