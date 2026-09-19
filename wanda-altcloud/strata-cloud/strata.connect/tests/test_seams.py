"""The RawStore and ReadingQueue seams (incl. the retry policy)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.device_readings import MAX_RECEIVE_COUNT

from app.services.raw_store import PostgresRawStore
from app.services.reading_queue import PostgresReadingQueue
from tests.sample_payloads import WEIGHT_PAYLOAD

store = PostgresRawStore()
queue = PostgresReadingQueue(retry_delay_seconds=0)


@pytest.mark.anyio
async def test_raw_store_round_trip(session: AsyncSession) -> None:
    """put → get returns the payload verbatim; the ref is opaque and persistent."""
    ref = await store.put(session, payload=WEIGHT_PAYLOAD, correlation_id="seam-raw-1")
    await session.commit()

    assert await store.get(session, ref) == WEIGHT_PAYLOAD


@pytest.mark.anyio
async def test_raw_store_dangling_ref(session: AsyncSession) -> None:
    """Fetching a raw payload by a dangling ref raises LookupError."""
    with pytest.raises(LookupError):
        await store.get(session, "no-such-ref")


@pytest.mark.anyio
async def test_dequeue_claims_oldest_and_counts_delivery(
    session: AsyncSession, drained: None
) -> None:
    """dequeue claims the oldest queued entry and counts the delivery attempt."""
    ref1 = await store.put(session, payload={"n": 1}, correlation_id="seam-q-oldest-1")
    await queue.enqueue(session, raw_ref=ref1, correlation_id="seam-q-oldest-1")
    await session.commit()
    ref2 = await store.put(session, payload={"n": 2}, correlation_id="seam-q-oldest-2")
    await queue.enqueue(session, raw_ref=ref2, correlation_id="seam-q-oldest-2")
    await session.commit()

    entry = await queue.dequeue(session)

    assert entry is not None
    assert entry.correlation_id == "seam-q-oldest-1"
    assert entry.state == "in_flight"
    assert entry.receive_count == 1
    await session.commit()


@pytest.mark.anyio
async def test_ack_marks_done(session: AsyncSession, drained: None) -> None:
    """ack persists the queue entry's state as done (proven by a Postgres re-read)."""
    ref = await store.put(session, payload={}, correlation_id="seam-q-ack")
    await queue.enqueue(session, raw_ref=ref, correlation_id="seam-q-ack")
    await session.commit()

    entry = await queue.dequeue(session)
    assert entry is not None and entry.correlation_id == "seam-q-ack"
    await queue.ack(session, entry)
    await session.commit()

    await session.refresh(entry)  # re-read from Postgres — prove the state was persisted
    assert entry.state == "done"


@pytest.mark.anyio
async def test_three_retries_then_undeliverable(session: AsyncSession, drained: None) -> None:
    """(b shape): first delivery + three retries, then undeliverable."""
    ref = await store.put(session, payload={}, correlation_id="seam-q-dlq")
    await queue.enqueue(session, raw_ref=ref, correlation_id="seam-q-dlq")
    await session.commit()

    for delivery in range(1, MAX_RECEIVE_COUNT + 1):
        entry = await queue.dequeue(session)
        assert entry is not None, f"delivery {delivery} should still be retryable"
        assert entry.receive_count == delivery
        await queue.fail(session, entry, error=f"boom {delivery}")
        await session.commit()
        await session.refresh(entry)  # re-read from Postgres, not the in-memory object
        expected = "undeliverable" if delivery == MAX_RECEIVE_COUNT else "ready"
        assert entry.state == expected

    assert entry.last_error == f"boom {MAX_RECEIVE_COUNT}"
    assert await queue.dequeue(session) is None  # nothing ready — it's dead-lettered


@pytest.mark.anyio
async def test_failed_message_invisible_until_retry_delay(
    session: AsyncSession, drained: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a failed message waits out the retry delay before redelivery
    a transient fault must not burn every retry in milliseconds."""
    from datetime import timedelta

    from app.clock import clock

    delayed_queue = PostgresReadingQueue(retry_delay_seconds=45)
    ref = await store.put(session, payload={}, correlation_id="seam-q-delay")
    await delayed_queue.enqueue(session, raw_ref=ref, correlation_id="seam-q-delay")
    await session.commit()

    entry = await delayed_queue.dequeue(session)
    assert entry is not None
    await delayed_queue.fail(session, entry, error="transient blip")
    await session.commit()

    assert entry.state == "ready"
    assert await delayed_queue.dequeue(session) is None  # ready, but not yet visible

    real_now = clock.now()
    monkeypatch.setattr(type(clock), "now", lambda self: real_now + timedelta(seconds=46))
    redelivered = await delayed_queue.dequeue(session)
    assert redelivered is not None and redelivered.correlation_id == "seam-q-delay"
    assert redelivered.receive_count == 2
