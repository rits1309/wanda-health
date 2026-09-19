"""The Processor + consumer.

End-to-end through the real surfaces: readings arrive via POST /v1/readings (the edge),
then the consumer claims, validates, resolves, and hands off through the Worker seam
(a recording stub here — the persisting Worker has its own suite, test_worker.py).
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.device_readings import (
    MAX_RECEIVE_COUNT,
    QuarantinedReading,
    ReadingQueueEntry,
)
from strata_core.fixtures import create_profile

from app.services.consumer import drain, process_one
from app.services.processor import Attributed
from app.services.registration import register_device
from tests.conftest import EDGE_SECRET
from tests.sample_payloads import BP_PAYLOAD, WEIGHT_PAYLOAD

AUTH = {"X-API-Key": EDGE_SECRET}


class RecordingWorker:
    """A recording stand-in on the seam — isolates Processor behaviour from persistence."""

    def __init__(self) -> None:
        self.handled: list[Attributed] = []

    async def handle(self, session: AsyncSession, attributed: Attributed) -> None:
        self.handled.append(attributed)


@pytest.fixture
async def member_with_bp_cuff(session: AsyncSession) -> str:
    """A Member holding the active registration for the sample payloads' device id."""
    profile = await create_profile(
        session,
        email="processor-member@wanda.test",
        first_name="Processor",
        last_name="Member",
        cognito_sub="processor-test-sub",
    )
    await register_device(
        session,
        user_id=profile.id,
        identifier_type="SmartMeter Blood Pressure",
        external_id=BP_PAYLOAD["device_id"],
    )
    await session.commit()
    return profile.id


def _ingest(client: TestClient, payload: object) -> str:
    response = client.post("/v1/readings", json=payload, headers=AUTH)
    assert response.status_code == 202
    return str(response.json()["correlation_id"])


async def _entry(session: AsyncSession, correlation_id: str) -> ReadingQueueEntry:
    return (
        await session.execute(
            select(ReadingQueueEntry).where(ReadingQueueEntry.correlation_id == correlation_id)
        )
    ).scalar_one()


@pytest.mark.anyio
async def test_registered_reading_attributed_and_acked(
    client: TestClient, session: AsyncSession, drained: None, member_with_bp_cuff: str
) -> None:
    """+ 2.2a: canonicalised, attributed to the registered Member, handed
    to the Worker in-process, and the message acknowledged."""
    correlation_id = _ingest(client, BP_PAYLOAD)
    worker = RecordingWorker()

    assert await process_one(worker=worker) is True

    (attributed,) = worker.handled
    assert attributed.user_id == member_with_bp_cuff
    assert attributed.correlation_id == correlation_id
    assert attributed.reading.systolic_mmhg == 128

    entry = await _entry(session, correlation_id)
    assert entry.state == "done"


@pytest.mark.anyio
async def test_unregistered_device_quarantined(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """quarantine record with correlation id + raw ref; no Member record;
    the message is acknowledged, not retried."""
    payload = {**WEIGHT_PAYLOAD, "device_id": "SM-NEVER-REGISTERED"}
    correlation_id = _ingest(client, payload)
    worker = RecordingWorker()

    assert await process_one(worker=worker) is True

    assert worker.handled == []  # nothing reached the Worker
    record = (
        await session.execute(
            select(QuarantinedReading).where(QuarantinedReading.correlation_id == correlation_id)
        )
    ).scalar_one()
    assert record.device_id == "SM-NEVER-REGISTERED"
    assert record.reading_type == "weight"
    assert record.state == "open"
    entry = await _entry(session, correlation_id)
    assert entry.raw_ref == record.raw_ref
    assert entry.state == "done"


@pytest.mark.anyio
async def test_quarantine_redelivery_creates_no_duplicate(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """Replay/redelivery before resolution finds the open record (at this surface)."""
    from app.services.raw_store import get_raw_store
    from app.services.reading_queue import get_reading_queue

    payload = {**WEIGHT_PAYLOAD, "device_id": "SM-QUARANTINE-TWICE"}
    correlation_id = _ingest(client, payload)
    assert await process_one(worker=RecordingWorker()) is True

    # Replay: re-enqueue the same raw payload under its original correlation id.
    entry = await _entry(session, correlation_id)
    await get_reading_queue().enqueue(session, raw_ref=entry.raw_ref, correlation_id=correlation_id)
    await session.commit()
    assert await process_one(worker=RecordingWorker()) is True

    open_records = (
        (
            await session.execute(
                select(QuarantinedReading).where(
                    QuarantinedReading.correlation_id == correlation_id,
                    QuarantinedReading.state == "open",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(open_records) == 1
    assert await get_raw_store().get(session, entry.raw_ref) == payload  # untouched


@pytest.mark.anyio
async def test_malformed_payload_retries_then_dead_letters(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """+ 4.1a/b: validation error recorded (shape only, — never the
    offending values), message retried, undeliverable after the final delivery."""
    payload = {**BP_PAYLOAD, "systolic_mmhg": "SECRET-CLINICAL-1", "reading_id": "not-an-int"}
    correlation_id = _ingest(client, payload)

    for delivery in range(1, MAX_RECEIVE_COUNT + 1):
        assert await process_one(worker=RecordingWorker()) is True
        entry = await _entry(session, correlation_id)
        await session.refresh(entry)  # re-read from Postgres, not the identity map
        assert entry.receive_count == delivery
        assert entry.last_error is not None
        assert "canonical validation failed" in entry.last_error
        assert "SECRET-CLINICAL-1" not in entry.last_error  # error shape, not values
        expected = "undeliverable" if delivery == MAX_RECEIVE_COUNT else "ready"
        assert entry.state == expected

    assert await process_one(worker=RecordingWorker()) is False  # nothing ready remains


@pytest.mark.anyio
async def test_drain_reports_handled_count(client: TestClient, drained: None) -> None:
    """drain returns how many queued readings it handled, then zero when the queue is empty."""
    _ingest(client, {**WEIGHT_PAYLOAD, "device_id": "SM-DRAIN-A"})
    _ingest(client, {**WEIGHT_PAYLOAD, "device_id": "SM-DRAIN-B"})

    assert await drain(worker=RecordingWorker()) == 2
    assert await drain(worker=RecordingWorker()) == 0


@pytest.mark.anyio
async def test_dev_endpoint_runs_pipeline_once(
    drained: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dev single-shot endpoint, mounted only under STRATA_DEV_MODE."""
    from app.config import settings
    from app.main import create_app

    # monkeypatch restores the pre-test value; a hardcoded reset can mask pollution.
    monkeypatch.setattr(settings, "dev_mode", True)
    dev_client = TestClient(create_app())
    ingested = _ingest(dev_client, {**WEIGHT_PAYLOAD, "device_id": "SM-DEV-RUN"})
    assert ingested
    response = dev_client.post("/v1/dev/pipeline/run")
    assert response.status_code == 200
    assert response.json()["processed"] == 1


def test_dev_endpoint_absent_by_default(client: TestClient) -> None:
    """The dev pipeline endpoint does not exist unless the dev seam is enabled."""
    assert client.post("/v1/dev/pipeline/run").status_code == 404


@pytest.mark.anyio
async def test_re_registration_takes_effect_for_later_readings(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """the resolver honours the registration window — a reading arriving
    after supersede attributes to the NEW Member, never the ended mapping's holder."""
    member_a = await create_profile(
        session,
        email="rereg-a@wanda.test",
        first_name="Rereg",
        last_name="A",
        cognito_sub="rereg-sub-a",
    )
    member_b = await create_profile(
        session,
        email="rereg-b@wanda.test",
        first_name="Rereg",
        last_name="B",
        cognito_sub="rereg-sub-b",
    )
    await register_device(
        session,
        user_id=member_a.id,
        identifier_type="SmartMeter Scale",
        external_id="SM-REREG-001",
    )
    await register_device(  # supersede: A's mapping is ended, B's becomes active
        session,
        user_id=member_b.id,
        identifier_type="SmartMeter Scale",
        external_id="SM-REREG-001",
    )
    await session.commit()
    _ingest(client, {**WEIGHT_PAYLOAD, "device_id": "SM-REREG-001"})
    worker = RecordingWorker()

    assert await process_one(worker=worker) is True

    (attributed,) = worker.handled
    assert attributed.user_id == member_b.id  # not the ended mapping's Member


@pytest.mark.anyio
async def test_weight_values_survive_attribution(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """through the pipeline: the attributed canonical reading still carries both
    unit systems exactly as supplied."""
    profile = await create_profile(
        session,
        email="processor-scale@wanda.test",
        first_name="Scale",
        last_name="Member",
        cognito_sub="processor-scale-sub",
    )
    await register_device(
        session,
        user_id=profile.id,
        identifier_type="SmartMeter Scale",
        external_id=WEIGHT_PAYLOAD["device_id"],
    )
    await session.commit()
    _ingest(client, WEIGHT_PAYLOAD)
    worker = RecordingWorker()

    assert await process_one(worker=worker) is True

    (attributed,) = worker.handled
    assert attributed.reading.weight_kg == Decimal("81.6")
    assert attributed.reading.weight_lbs == Decimal("180.0")
