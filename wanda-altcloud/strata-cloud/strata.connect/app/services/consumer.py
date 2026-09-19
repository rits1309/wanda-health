"""The pipeline consumer: claim → process → ack/fail, one message at a time.

The claim policy comes from the queue seam's contract (see reading_queue.py): the dequeue
is **committed in its own transaction before processing begins**, so a crash mid-processing
can never roll back the ``receive_count`` increment — a poison message therefore counts
every delivery and reaches undeliverable instead of redelivering forever. The
trade-off (a hard kill leaves the entry ``in_flight``, visible via the listings) is the
recoverable failure mode, chosen deliberately at the review.

Every handled error becomes ``fail()`` — no exception may skip it — and the correlation id
is bound into structlog contextvars for the life of the message, so every log line
of a message's processing carries it. Runtimes: the standalone loop (``inv pipeline-run``,
``python -m app.services.consumer``), the optional in-process loop (``settings.pipeline_
interval_seconds`` > 0, booking's jobs pattern), and the dev single-shot endpoint.
"""

import asyncio

import structlog
from strata_core.domains.device_readings import ReadingQueueEntry

from app.db import init_engine, session_factory
from app.services.processor import Attributed, Dropped, SchemaValidationFailure, process
from app.services.raw_store import RawStore, get_raw_store
from app.services.reading_queue import ReadingQueue, get_reading_queue
from app.services.worker import Worker, get_worker

_log = structlog.get_logger(__name__)


async def process_one(
    store: RawStore | None = None,
    queue: ReadingQueue | None = None,
    worker: Worker | None = None,
) -> bool:
    """Handle at most one queue message; returns False when the queue had nothing ready.

    A returned True means the message reached a terminal outcome for this delivery:
    acknowledged (persisted or quarantined) or failed (retry / undeliverable).
    """
    store = store or get_raw_store()
    queue = queue or get_reading_queue()
    worker = worker or get_worker()
    factory = session_factory()

    # Transaction 1 — the committed claim.
    async with factory() as session:
        entry = await queue.dequeue(session)
        if entry is None:
            return False
        await session.commit()
        entry_id, correlation_id = entry.id, entry.correlation_id

    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    try:
        # Transaction 2 — process + outcome, atomically with the ack.
        try:
            async with factory() as session:
                claimed = await session.get_one(ReadingQueueEntry, entry_id)
                outcome = await process(session, store, claimed)
                if isinstance(outcome, Attributed):
                    await worker.handle(session, outcome)
                    _log.info("reading attributed", raw_ref=outcome.raw_ref)
                elif isinstance(outcome, Dropped):
                    # An intentional, visible no-op — the operator discarded this
                    # correlation (never-to-be-processed), so the message dies acked.
                    _log.info("discarded correlation dropped", quarantine_id=outcome.quarantine_id)
                else:
                    _log.info("reading quarantined", quarantine_id=outcome.quarantine_id)
                await queue.ack(session, claimed)
                await session.commit()
        except SchemaValidationFailure as exc:
            # error recorded against the correlation id, message to retry/DLQ.
            _log.warning("reading failed validation", error=str(exc))
            await _fail(queue, entry_id, str(exc))
        except Exception as exc:
            # Transient faults: the message must retry, never vanish.
            _log.exception("reading processing failed")
            await _fail(queue, entry_id, f"{type(exc).__name__}: {exc}")
        return True
    finally:
        structlog.contextvars.unbind_contextvars("correlation_id")


async def _fail(queue: ReadingQueue, entry_id: str, error: str) -> None:
    """Record the failure in a fresh transaction (the processing one has rolled back).

    If even this write fails (the same outage that broke processing), swallow and log:
    the entry stays ``in_flight`` — the documented, -visible crash mode — and the
    loop lives to process the rest of the queue when the fault clears.
    """
    try:
        async with session_factory()() as session:
            entry = await session.get_one(ReadingQueueEntry, entry_id)
            await queue.fail(session, entry, error=error)
            await session.commit()
    except Exception:
        _log.exception("could not record failure; entry left in_flight", entry_id=entry_id)


async def drain(limit: int = 100, worker: Worker | None = None) -> int:
    """Process until the queue is empty (or the safety limit); returns messages handled."""
    handled = 0
    while handled < limit and await process_one(worker=worker):
        handled += 1
    return handled


async def run_loop(poll_seconds: float, worker: Worker | None = None) -> None:
    """Poll forever: drain what's ready, sleep, repeat. Cancelled at shutdown.

    Each tick is exception-guarded (booking's jobs-loop pattern): a transient fault —
    including one during the claim itself, before process_one's own handling starts —
    must cost one tick, never the loop. A dead consumer with a live ingest edge would
    silently pile up unprocessed readings.
    """
    _log.info("pipeline consumer started", poll_seconds=poll_seconds)
    while True:
        try:
            await drain(worker=worker)
        except Exception:
            # Keep the loop alive; the next tick retries (claims roll back whole).
            _log.exception("pipeline tick failed")
        await asyncio.sleep(poll_seconds)


async def _main() -> None:  # pragma: no cover — the `inv pipeline-run` entry point
    from app.config import settings
    from app.observability import configure_logging

    configure_logging(settings)
    engine = init_engine()
    try:
        await run_loop(settings.pipeline_poll_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
