"""Pipeline tables: the Phase 1 seams, quarantine, and the domain's outbox.

- ``raw_readings``: the Phase 1 ``RawStore`` — the inbound payload verbatim
  (JSONB); ``id`` is the ``raw_ref`` every reading row traces back to.
  Replaced by S3 in Phase 2, hence its own table rather than columns on the
  readings.
- ``reading_queue``: the Phase 1 ``ReadingQueue`` — three automated retries
  then ``undeliverable`` (the SQS ``maxReceiveCount 4`` → DLQ analogue in
  Phase 2). ``correlation_id`` is deliberately not unique here: replay
  re-enqueues the same correlation.
- ``quarantined_readings``: the unregistered-device holding pen
   — points at the raw payload and copies no clinical values.
- ``readings_outbox_events``: mirrors booking's ``outbox_events`` shape; a
  per-domain table because the ownership registry allows one writer per domain
. ``published_at`` stays NULL — no relay while are open.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id

#: The queue-ticket lifecycle — the writer (`strata.connect`) imports these rather than
#: hard-coding strings that would silently never match (there is no CHECK constraint).
READING_QUEUE_STATES: Final[tuple[str, ...]] = ("ready", "in_flight", "done", "undeliverable")

#: a ticket is received at most this many times — the first delivery plus three
#: automated retries — then marked undeliverable (the SQS ``maxReceiveCount 4`` → DLQ
#: analogue in Phase 2). The Processor's retry loop imports this, never its own copy.
MAX_RECEIVE_COUNT: Final[int] = 4

#: The quarantine lifecycle: open until the device registers and the
#: reading is replayed (resolved) or an operator discards it.
QUARANTINE_STATES: Final[tuple[str, ...]] = ("open", "resolved", "discarded")

#: The closed list of reading types: a third type is a requirements change.
READING_TYPES: Final[tuple[str, ...]] = ("weight", "blood_pressure")


class RawReading(TimestampMixin, Base):
    __tablename__ = "raw_readings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReadingQueueEntry(TimestampMixin, Base):
    __tablename__ = "reading_queue"
    __table_args__ = (
        # The consumer's dequeue poll runs continuously while done/undeliverable rows
        # accumulate forever (they are the listings' data) — index only the live ones.
        Index(
            "ix_reading_queue_ready",
            "state",
            postgresql_where=text("state = 'ready'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    raw_ref: Mapped[str] = mapped_column(ForeignKey("raw_readings.id"), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="ready")
    receive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    # SQS's visibility-timeout property, which retries need to mean anything for
    # transient faults: a failed message becomes claimable again only
    # after this instant. NULL = immediately visible (fresh enqueues).
    visible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuarantinedReading(TimestampMixin, Base):
    __tablename__ = "quarantined_readings"
    __table_args__ = (
        # Redelivery before resolution must not duplicate the quarantine (idempotency
        # at this surface): at most one OPEN row per correlation; resolved history survives.
        Index(
            "uq_quarantined_readings_open",
            "correlation_id",
            unique=True,
            postgresql_where=text("state = 'open'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    correlation_id: Mapped[str] = mapped_column(String, nullable=False)
    raw_ref: Mapped[str] = mapped_column(ForeignKey("raw_readings.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    reading_type: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="open")


class ReadingsOutboxEvent(TimestampMixin, Base):
    __tablename__ = "readings_outbox_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
