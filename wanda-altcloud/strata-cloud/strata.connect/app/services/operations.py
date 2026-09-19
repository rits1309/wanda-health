"""Operator tooling: replay, listings, quarantine discard.

**Replay** re-enqueues a captured raw payload under its ORIGINAL
correlation id, so the outcome is identical to first-time processing: the Worker's
dedup makes a replay of already-persisted work a no-op, and a replay after a fix
(a registered device, a cleared outage) completes what the first attempt could not. This
is the recovery handle for every terminal failure mode — undeliverable messages, stuck
``in_flight`` claims from a hard-killed consumer, and quarantined readings once their
device is registered (the recommended manual flow — the question is formally
still open; the Processor resolves the open record when the replay attributes). The one
exception: a DISCARDED correlation is refused — that exit means never-to-be-processed,
and the word holds everywhere: replay refuses to enqueue, discard cancels any entries
already pending, and the Processor drops (ack + log) anything that slips through the
race — so a replay enqueued *before* the discard dies with it (the epic-gate finding).

**Listings** surface what needs an operator: undeliverable messages ('s "alert on
undeliverable depth" stands in Phase 1 as a listing to check), possibly-stuck in-flight
claims, and open quarantine records. Identifiers only: correlation ids, states,
counts, and error *shapes* — never payload values.

Everything here flushes and never commits (the service-layer norm); the CLI entry point
below and the dev router own their transactions. Operator surface: ``inv replay /
undeliverable / quarantine / quarantine-discard`` (API/CLI only).
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from strata_core.domains.device_readings import (
    QUARANTINE_STATES,
    READING_QUEUE_STATES,
    QuarantinedReading,
    ReadingQueueEntry,
)

from app.services.raw_store import get_raw_store
from app.services.reading_queue import get_reading_queue

_log = structlog.get_logger(__name__)

# The kernel owns both state vocabularies; bind positionally so drift fails loudly here.
_READY, _IN_FLIGHT, _DONE, _UNDELIVERABLE = READING_QUEUE_STATES
_QUARANTINE_OPEN, _, _QUARANTINE_DISCARDED = QUARANTINE_STATES


class ReplayRefused(Exception):
    """The payload exists but replay must not run — currently only the discarded case:
    an operator closed the quarantine record as never-to-be-processed, and that word
    has to hold against a later replay too. Discard is final in Phase 1 (no un-discard)."""


@dataclass(frozen=True)
class ReplayReceipt:
    """What a replay did: the fresh queue entry, plus the correlation's delivery history
    (state → entry count) so the operator sees what they are replaying over."""

    entry_id: str
    correlation_id: str
    prior_entries: dict[str, int]


@dataclass(frozen=True)
class QueueEntrySummary:
    """One queue entry, identifiers only — ``last_error`` is an error *shape*
    by construction (processor records loc+type, engine hides bound parameters)."""

    id: str
    correlation_id: str
    state: str
    receive_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class QuarantineSummary:
    """One quarantine record — points at the raw payload, copies no clinical values."""

    id: str
    correlation_id: str
    device_id: str
    reading_type: str
    state: str
    created_at: datetime


async def replay(session: AsyncSession, correlation_id: str) -> ReplayReceipt:
    """Re-enqueue the raw payload captured under ``correlation_id``.

    Raises ``LookupError`` when nothing was ever captured under that id, and
    ``ReplayRefused`` when its quarantine record was discarded (the one exit that must
    stay closed). Deliberately unconditional otherwise — replaying completed work is
    safe, and the receipt's ``prior_entries`` tells the operator what
    history the replay joins.
    """
    raw_ref = await get_raw_store().ref_for_correlation(session, correlation_id)
    if raw_ref is None:
        raise LookupError(f"no raw reading captured under correlation id {correlation_id!r}")

    discarded = (
        await session.execute(
            select(QuarantinedReading.id).where(
                QuarantinedReading.correlation_id == correlation_id,
                QuarantinedReading.state == _QUARANTINE_DISCARDED,
            )
        )
    ).first()
    if discarded is not None:
        raise ReplayRefused(
            f"correlation id {correlation_id!r} was discarded (never-to-be-processed);"
            " discard is final in Phase 1"
        )

    prior = {
        state: count
        for state, count in (
            await session.execute(
                select(ReadingQueueEntry.state, func.count())
                .where(ReadingQueueEntry.correlation_id == correlation_id)
                .group_by(ReadingQueueEntry.state)
            )
        ).all()
    }
    entry = await get_reading_queue().enqueue(
        session, raw_ref=raw_ref, correlation_id=correlation_id
    )
    _log.info("reading replayed", entry_id=entry.id, prior_entries=prior)
    return ReplayReceipt(entry_id=entry.id, correlation_id=correlation_id, prior_entries=prior)


async def undeliverable_entries(session: AsyncSession) -> list[QueueEntrySummary]:
    """Messages that exhausted their retries — each one awaits a fix and a replay."""
    return await _queue_entries(session, _UNDELIVERABLE)


async def in_flight_entries(session: AsyncSession) -> list[QueueEntrySummary]:
    """Claims that never reached an outcome — the committed-claim policy's documented
    crash mode (a hard-killed consumer, or a failure the fail-writer couldn't record).
    A live consumer holds a claim only for milliseconds; anything listed here for
    longer is stuck, and replay is the recovery."""
    return await _queue_entries(session, _IN_FLIGHT)


async def _queue_entries(session: AsyncSession, state: str) -> list[QueueEntrySummary]:
    """Entries in ``state`` whose correlation has NOT since reached ``done``: the
    listings are live worklists, so a correlation recovered by a later successful
    delivery (a replay) sheds its stale undeliverable/in_flight history. Without
    this, 's depth signal only ever grows and every recovered reading haunts
    the listing forever (panel finding at). The stale rows themselves are
    retained — history, not worklist."""
    recovered = aliased(ReadingQueueEntry)
    rows = (
        (
            await session.execute(
                select(ReadingQueueEntry)
                .where(
                    ReadingQueueEntry.state == state,
                    ~select(recovered.id)
                    .where(
                        recovered.correlation_id == ReadingQueueEntry.correlation_id,
                        recovered.state == _DONE,
                    )
                    .exists(),
                )
                .order_by(ReadingQueueEntry.created_at, ReadingQueueEntry.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        QueueEntrySummary(
            id=row.id,
            correlation_id=row.correlation_id,
            state=row.state,
            receive_count=row.receive_count,
            last_error=row.last_error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


async def quarantined_readings(
    session: AsyncSession, state: str = _QUARANTINE_OPEN
) -> list[QuarantineSummary]:
    """Quarantine records by state — ``open`` (the default) is the operator's worklist:
    register the device, then replay ('s manual resolution flow)."""
    rows = (
        (
            await session.execute(
                select(QuarantinedReading)
                .where(QuarantinedReading.state == state)
                .order_by(QuarantinedReading.created_at, QuarantinedReading.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        QuarantineSummary(
            id=row.id,
            correlation_id=row.correlation_id,
            device_id=row.device_id,
            reading_type=row.reading_type,
            state=row.state,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def discard_quarantined(session: AsyncSession, correlation_id: str) -> str:
    """Close an open quarantine record as never-to-be-processed (the operator's other
    exit from the worklist). Raises ``LookupError`` when no open record exists."""
    record = (
        await session.execute(
            select(QuarantinedReading).where(
                QuarantinedReading.correlation_id == correlation_id,
                QuarantinedReading.state == _QUARANTINE_OPEN,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise LookupError(f"no open quarantine record for correlation id {correlation_id!r}")
    record.state = _QUARANTINE_DISCARDED

    # Discard is terminal for the whole correlation: a replay enqueued while the record
    # was still open must die with it, not fire later (the epic-gate finding). Pending
    # entries are closed here; the Processor's own discard check covers one that a
    # consumer has already claimed.
    pending = (
        (
            await session.execute(
                select(ReadingQueueEntry).where(
                    ReadingQueueEntry.correlation_id == correlation_id,
                    ReadingQueueEntry.state.in_((_READY, _IN_FLIGHT)),
                )
            )
        )
        .scalars()
        .all()
    )
    for entry in pending:
        entry.state = _DONE
        entry.last_error = "cancelled: correlation discarded"

    await session.flush()
    _log.info("quarantine discarded", quarantine_id=record.id, cancelled_entries=len(pending))
    return record.id


# --- CLI (`python -m app.services.operations`, wrapped by the inv tasks) -----------------


def _print_queue_section(title: str, entries: list[QueueEntrySummary]) -> None:  # pragma: no cover
    print(f"{title} ({len(entries)}):")
    for e in entries:
        error = e.last_error or "-"
        print(
            f"  {e.correlation_id}  receives={e.receive_count}"
            f"  created={e.created_at:%Y-%m-%dT%H:%M:%SZ}"
            f"  updated={e.updated_at:%Y-%m-%dT%H:%M:%SZ}  error={error}"
        )
    if not entries:
        print("  (none)")


async def _run(command: str, correlation_id: str | None) -> int:  # pragma: no cover
    from app.db import init_engine, session_factory

    engine = init_engine()
    try:
        async with session_factory()() as session:
            try:
                # argparse enforces --correlation-id on the two commands that narrow on it.
                if command == "replay" and correlation_id is not None:
                    receipt = await replay(session, correlation_id)
                    await session.commit()
                    print(
                        f"re-enqueued {receipt.correlation_id} as queue entry"
                        f" {receipt.entry_id} (run the pipeline to process it)"
                    )
                    if receipt.prior_entries:
                        history = ", ".join(
                            f"{state}={count}"
                            for state, count in sorted(receipt.prior_entries.items())
                        )
                        print(f"prior queue entries: {history}")
                elif command == "undeliverable":
                    _print_queue_section(
                        "undeliverable — retries exhausted, fix then `inv replay` ",
                        await undeliverable_entries(session),
                    )
                    _print_queue_section(
                        "in_flight — possibly stuck claims (crashed consumer?)",
                        await in_flight_entries(session),
                    )
                elif command == "quarantine":
                    records = await quarantined_readings(session)
                    print(
                        "open quarantine — register the device, then"
                        f" `inv replay` ({len(records)}):"
                    )
                    for q in records:
                        print(
                            f"  {q.correlation_id}  device={q.device_id}"
                            f"  type={q.reading_type}  created={q.created_at:%Y-%m-%dT%H:%M:%SZ}"
                        )
                    if not records:
                        print("  (none)")
                elif command == "quarantine-discard" and correlation_id is not None:
                    quarantine_id = await discard_quarantined(session, correlation_id)
                    await session.commit()
                    print(f"discarded quarantine record {quarantine_id}")
                else:
                    print(f"error: unknown command {command!r}", file=sys.stderr)
                    return 2
            except (LookupError, ReplayRefused) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
    finally:
        await engine.dispose()
    return 0


def _main() -> int:  # pragma: no cover — the inv-task entry point
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m app.services.operations",
        description="Operator tooling: replay + listings.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    replay_p = sub.add_parser(
        "replay", help="re-enqueue a captured payload under its original correlation id"
    )
    replay_p.add_argument("--correlation-id", required=True)
    sub.add_parser("undeliverable", help="list undeliverable messages and stuck claims")
    sub.add_parser("quarantine", help="list open quarantine records")
    discard_p = sub.add_parser(
        "quarantine-discard", help="close an open quarantine record as never-to-be-processed"
    )
    discard_p.add_argument("--correlation-id", required=True)
    args = parser.parse_args()
    return asyncio.run(_run(args.command, getattr(args, "correlation_id", None)))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
