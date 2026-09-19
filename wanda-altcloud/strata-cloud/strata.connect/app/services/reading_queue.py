"""The ReadingQueue seam: enqueue / dequeue / ack / fail.

Phase 1 backs it with the kernel's ``reading_queue`` table — a real DLQ shape to test
against: ``receive_count`` counts deliveries, and a message that fails on its
``MAX_RECEIVE_COUNT``-th delivery (first + three automated retries) goes
**undeliverable** instead of back to ready. Phase 2 swaps in SQS with maxReceiveCount 4 →
DLQ; callers hold only the seam.

Retries are **spaced, not immediate**: ``fail()`` stamps ``visible_at`` (the retry delay,
mirroring SQS's visibility timeout) and ``dequeue`` skips entries whose time hasn't come —
without this, a transient fault (database blip, store timeout) would burn
all three retries in milliseconds and dead-letter readings a few seconds' patience would
have saved. Found by manual testing at.

The delay does NOT recover a crashed consumer's claim, so the consumer must still handle
both crash modes deliberately: (1) convert every handled error into ``fail()`` — never let
an exception skip it; (2) **commit the claim in its own transaction before processing** —
if the claim shares the processing transaction, a crash rolls back the ``receive_count``
increment and a poison message redelivers forever without ever reaching undeliverable
(silently defeated). The committed-claim trade-off — a hard kill mid-processing
leaves the entry ``in_flight`` — is the visible-and-recoverable failure mode: the
operational listings surface stuck entries for manual replay.
"""

from datetime import timedelta
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.device_readings import (
    MAX_RECEIVE_COUNT,
    READING_QUEUE_STATES,
    ReadingQueueEntry,
)

from app.clock import clock
from app.config import settings

# The kernel owns the state vocabulary; bind names positionally so drift fails loudly here.
_READY, _IN_FLIGHT, _DONE, _UNDELIVERABLE = READING_QUEUE_STATES


class ReadingQueue(Protocol):
    """What the ingest edge (enqueue) and the consumer loop (the rest) depend on."""

    async def enqueue(
        self, session: AsyncSession, *, raw_ref: str, correlation_id: str
    ) -> ReadingQueueEntry: ...

    async def dequeue(self, session: AsyncSession) -> ReadingQueueEntry | None: ...

    async def ack(self, session: AsyncSession, entry: ReadingQueueEntry) -> None: ...

    async def fail(
        self, session: AsyncSession, entry: ReadingQueueEntry, *, error: str
    ) -> None: ...


class PostgresReadingQueue:
    """Phase 1 implementation over the ``reading_queue`` table."""

    def __init__(self, retry_delay_seconds: float) -> None:
        self._retry_delay = timedelta(seconds=retry_delay_seconds)

    async def enqueue(
        self, session: AsyncSession, *, raw_ref: str, correlation_id: str
    ) -> ReadingQueueEntry:
        """Add a reference message; deliberately NOT unique per correlation (replay re-enqueues)."""
        entry = ReadingQueueEntry(raw_ref=raw_ref, correlation_id=correlation_id)
        session.add(entry)
        await session.flush()
        return entry

    async def dequeue(self, session: AsyncSession) -> ReadingQueueEntry | None:
        """Claim the oldest *visible* ready message (row-locked against concurrent consumers).

        Claiming counts as a delivery: ``receive_count`` increments here, exactly like SQS's
        ApproximateReceiveCount. Entries failed less than the retry delay ago are skipped.
        """
        entry = (
            await session.execute(
                select(ReadingQueueEntry)
                # Oldest-first is best-effort, as in Phase 2's SQS standard queues; the id
                # tie-break only makes ordering deterministic when created_at collides
                # (server_default now() is the transaction timestamp).
                .where(
                    ReadingQueueEntry.state == _READY,
                    or_(
                        ReadingQueueEntry.visible_at.is_(None),
                        ReadingQueueEntry.visible_at <= clock.now(),
                    ),
                )
                .order_by(ReadingQueueEntry.created_at, ReadingQueueEntry.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if entry is None:
            return None
        entry.state = _IN_FLIGHT
        entry.receive_count += 1
        await session.flush()
        return entry

    async def ack(self, session: AsyncSession, entry: ReadingQueueEntry) -> None:
        """Mark the message done; done rows are retained (they are the listings' data)."""
        entry.state = _DONE
        await session.flush()

    async def fail(self, session: AsyncSession, entry: ReadingQueueEntry, *, error: str) -> None:
        """Record the failure: back to ready after the retry delay, or undeliverable."""
        entry.last_error = error
        if entry.receive_count >= MAX_RECEIVE_COUNT:
            entry.state = _UNDELIVERABLE
        else:
            entry.state = _READY
            entry.visible_at = clock.now() + self._retry_delay
        await session.flush()


_queue = PostgresReadingQueue(retry_delay_seconds=settings.pipeline_retry_delay_seconds)


def get_reading_queue() -> ReadingQueue:
    """FastAPI dependency — routes depend on the seam, not the implementation."""
    return _queue
