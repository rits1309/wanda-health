"""The Worker — end-to-end through
the edge and the consumer's default (persisting) worker."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.device_readings import (
    BloodPressureReading,
    ReadingQueueEntry,
    ReadingsOutboxEvent,
    WeightReading,
)
from strata_core.fixtures import create_profile

from app.services.consumer import process_one
from app.services.registration import register_device
from tests.conftest import EDGE_SECRET
from tests.sample_payloads import BP_PAYLOAD, WEIGHT_PAYLOAD

AUTH = {"X-API-Key": EDGE_SECRET}


@pytest.fixture
async def member(session: AsyncSession) -> str:
    """One Member holding active registrations for BOTH sample devices' types."""
    profile = await create_profile(
        session,
        email="worker-member@wanda.test",
        first_name="Worker",
        last_name="Member",
        cognito_sub="worker-test-sub",
    )
    for identifier_type in ("SmartMeter Blood Pressure", "SmartMeter Scale"):
        await register_device(
            session,
            user_id=profile.id,
            identifier_type=identifier_type,
            external_id=str(BP_PAYLOAD["device_id"]),
        )
    await session.commit()
    return profile.id


def _ingest(client: TestClient, payload: object) -> str:
    response = client.post("/v1/readings", json=payload, headers=AUTH)
    assert response.status_code == 202
    return str(response.json()["correlation_id"])


async def _counts(session: AsyncSession, correlation_id: str) -> tuple[int, int]:
    rows = (
        await session.execute(
            select(func.count())
            .select_from(BloodPressureReading)
            .where(BloodPressureReading.correlation_id == correlation_id)
        )
    ).scalar_one()
    events = (
        await session.execute(
            select(func.count())
            .select_from(ReadingsOutboxEvent)
            .where(ReadingsOutboxEvent.payload["correlation_id"].astext == correlation_id)
        )
    ).scalar_one()
    return rows, events


@pytest.mark.anyio
async def test_bp_reading_persisted_with_outbox_event(
    client: TestClient, session: AsyncSession, drained: None, member: str
) -> None:
    """the reading row and its business event, in the same transaction,
    with values exactly as supplied, UTC, and full trace-back."""
    correlation_id = _ingest(client, BP_PAYLOAD)

    assert await process_one() is True  # the default worker now persists

    row = (
        await session.execute(
            select(BloodPressureReading).where(
                BloodPressureReading.correlation_id == correlation_id
            )
        )
    ).scalar_one()
    assert row.user_id == member
    assert row.provider_reading_id == "12345"  # StrictInt → String at the seam
    assert (row.systolic_mmhg, row.diastolic_mmhg, row.pulse_bpm) == (128, 82, 71)
    assert row.irregular is False
    assert row.suspect is False
    assert row.recorded_at == datetime(2026, 7, 1, 14, 32, tzinfo=UTC)
    assert row.raw_ref  # traces back to the verbatim payload

    event = (
        await session.execute(
            select(ReadingsOutboxEvent).where(
                ReadingsOutboxEvent.payload["correlation_id"].astext == correlation_id
            )
        )
    ).scalar_one()
    assert event.event_type == "reading.received"
    assert event.payload["reading_row_id"] == row.id
    assert event.published_at is None  # the relay is gated on

    entry = (
        await session.execute(
            select(ReadingQueueEntry).where(ReadingQueueEntry.correlation_id == correlation_id)
        )
    ).scalar_one()
    assert entry.state == "done"


@pytest.mark.anyio
async def test_replay_same_correlation_writes_nothing(
    client: TestClient, session: AsyncSession, drained: None, member: str
) -> None:
    """+ 4.1c: replay under the original correlation id
    completes like a first-time delivery, with no duplicate state."""
    from app.services.reading_queue import get_reading_queue

    # A test-unique provider id: dedup is global, so reusing 12345 would collide
    # with the row test_bp_reading_persisted... already created.
    correlation_id = _ingest(client, {**BP_PAYLOAD, "reading_id": 77002})
    assert await process_one() is True

    entry = (
        await session.execute(
            select(ReadingQueueEntry).where(ReadingQueueEntry.correlation_id == correlation_id)
        )
    ).scalar_one()
    await get_reading_queue().enqueue(session, raw_ref=entry.raw_ref, correlation_id=correlation_id)
    await session.commit()
    assert await process_one() is True  # the replay delivery

    assert await _counts(session, correlation_id) == (1, 1)  # one row, one event — still


@pytest.mark.anyio
async def test_provider_resend_writes_nothing(
    client: TestClient, session: AsyncSession, drained: None, member: str
) -> None:
    """the provider re-sends the same reading (same reading_id, fresh delivery and
    correlation id) — deduped on the globally unique provider identity."""
    payload = {**BP_PAYLOAD, "reading_id": 77003}  # test-unique (dedup is global)
    first = _ingest(client, payload)
    assert await process_one() is True
    resend = _ingest(client, payload)  # new correlation, same reading_id

    assert await process_one() is True

    rows = (
        await session.execute(
            select(func.count())
            .select_from(BloodPressureReading)
            .where(BloodPressureReading.provider_reading_id == "77003")
        )
    ).scalar_one()
    assert rows == 1
    assert (await _counts(session, resend))[0] == 0  # the resend created nothing
    assert first != resend
    entry = (
        await session.execute(
            select(ReadingQueueEntry).where(ReadingQueueEntry.correlation_id == resend)
        )
    ).scalar_one()
    assert entry.state == "done"  # acknowledged as handled, not retried


@pytest.mark.anyio
async def test_weight_example_persisted_verbatim(
    client: TestClient, session: AsyncSession, drained: None, member: str
) -> None:
    """BOTH example payloads verbatim through to rows:
    the in-bounds weight example, every field exactly as supplied incl. both tares."""
    correlation_id = _ingest(client, WEIGHT_PAYLOAD)

    assert await process_one() is True

    row = (
        await session.execute(
            select(WeightReading).where(WeightReading.correlation_id == correlation_id)
        )
    ).scalar_one()
    assert row.user_id == member
    assert row.provider_reading_id == "12346"
    assert row.weight_kg == Decimal("81.6")
    assert row.tare_kg == Decimal("0.0")
    assert row.weight_lbs == Decimal("180.0")
    assert row.tare_lbs == Decimal("0.0")
    assert row.suspect is False
    assert row.recorded_at == datetime(2026, 7, 1, 8, 15, tzinfo=UTC)


@pytest.mark.anyio
async def test_implausible_weight_persisted_as_suspect(
    client: TestClient, session: AsyncSession, drained: None, member: str
) -> None:
    """Out-of-bounds values are flagged and stored, never dropped;
    both unit systems land exactly as supplied."""
    payload = {**WEIGHT_PAYLOAD, "weight_kg": 8, "weight_lbs": 17.6, "reading_id": 77001}
    correlation_id = _ingest(client, payload)

    assert await process_one() is True

    row = (
        await session.execute(
            select(WeightReading).where(WeightReading.correlation_id == correlation_id)
        )
    ).scalar_one()
    assert row.suspect is True
    assert row.weight_kg == Decimal("8")
    assert row.weight_lbs == Decimal("17.6")
    assert row.tare_kg == Decimal("0.0")
    assert row.user_id == member


@pytest.mark.anyio
async def test_persisted_reading_unchanged_after_reregistration(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """(append-only) via 's AND-clause: a later reading landing for
    the device's NEW Member leaves the earlier Member's persisted row untouched, column
    for column — the pipeline only ever inserts reading rows."""
    member_a = (
        await create_profile(
            session,
            email="appendonly-a@wanda.test",
            first_name="Append",
            last_name="A",
            cognito_sub="ao-a",
        )
    ).id
    member_b = (
        await create_profile(
            session,
            email="appendonly-b@wanda.test",
            first_name="Append",
            last_name="B",
            cognito_sub="ao-b",
        )
    ).id
    await register_device(
        session,
        user_id=member_a,
        identifier_type="SmartMeter Scale",
        external_id="SM-APPEND-001",
    )
    await session.commit()
    _ingest(client, {**WEIGHT_PAYLOAD, "reading_id": 77201, "device_id": "SM-APPEND-001"})
    assert await process_one() is True

    row_a = (
        await session.execute(select(WeightReading).where(WeightReading.user_id == member_a))
    ).scalar_one()
    snapshot = {c.name: getattr(row_a, c.name) for c in WeightReading.__table__.columns}

    await register_device(  # supersede: A's mapping ends, B's becomes active
        session,
        user_id=member_b,
        identifier_type="SmartMeter Scale",
        external_id="SM-APPEND-001",
    )
    await session.commit()
    _ingest(client, {**WEIGHT_PAYLOAD, "reading_id": 77202, "device_id": "SM-APPEND-001"})
    assert await process_one() is True

    session.expire_all()
    row_b = (
        await session.execute(select(WeightReading).where(WeightReading.user_id == member_b))
    ).scalar_one()
    assert row_b.provider_reading_id == "77202"  # the later reading followed the new mapping
    row_a_after = (
        await session.execute(select(WeightReading).where(WeightReading.user_id == member_a))
    ).scalar_one()
    assert {c.name: getattr(row_a_after, c.name) for c in WeightReading.__table__.columns} == (
        snapshot
    )
