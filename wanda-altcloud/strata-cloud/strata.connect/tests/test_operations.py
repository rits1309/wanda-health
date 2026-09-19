"""Operator tooling: replay, the undeliverable and
quarantine listings, and the manual resolution flow (register the device, replay).

Reading ids here are test-unique in the 771xx range (dedup is global) and devices
carry an SM-OPS- prefix so registrations never collide with other modules' members.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.device_readings import (
    MAX_RECEIVE_COUNT,
    BloodPressureReading,
    QuarantinedReading,
    ReadingQueueEntry,
)
from strata_core.fixtures import create_profile

from app.services.consumer import drain
from app.services.operations import (
    ReplayRefused,
    discard_quarantined,
    in_flight_entries,
    quarantined_readings,
    replay,
    undeliverable_entries,
)
from app.services.processor import Attributed
from app.services.raw_store import get_raw_store
from app.services.reading_queue import get_reading_queue
from app.services.registration import register_device
from tests.conftest import EDGE_SECRET
from tests.sample_payloads import BP_PAYLOAD

AUTH = {"X-API-Key": EDGE_SECRET}


class BoomWorker:
    """A worker mid-outage: every attributed reading fails (transient fault, 4.1a)."""

    async def handle(self, session: AsyncSession, attributed: Attributed) -> None:
        raise RuntimeError("worker outage (test)")


def _ingest(client: TestClient, payload: object) -> str:
    response = client.post("/v1/readings", json=payload, headers=AUTH)
    assert response.status_code == 202
    return str(response.json()["correlation_id"])


async def _register(session: AsyncSession, *, email: str, device_id: str) -> str:
    profile = await create_profile(
        session, email=email, first_name="Ops", last_name="Member", cognito_sub=f"ops-{email}"
    )
    await register_device(
        session,
        user_id=profile.id,
        identifier_type="SmartMeter Blood Pressure",
        external_id=device_id,
    )
    await session.commit()
    return profile.id


async def _entry_states(session: AsyncSession, correlation_id: str) -> list[str]:
    return list(
        (
            await session.execute(
                select(ReadingQueueEntry.state)
                .where(ReadingQueueEntry.correlation_id == correlation_id)
                .order_by(ReadingQueueEntry.created_at, ReadingQueueEntry.id)
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.anyio
async def test_replay_recovers_a_dead_letter(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """retries exhausted during an outage, the fault clears, the
    operator replays under the original correlation id — the reading lands."""
    device = "SM-OPS-DEADLETTER"
    user_id = await _register(session, email="ops-deadletter@wanda.test", device_id=device)
    payload = {**BP_PAYLOAD, "device_id": device, "reading_id": 77101}
    correlation_id = _ingest(client, payload)

    # The outage: the retry delay is 0 suite-wide, so one drain burns the whole ladder.
    await drain(worker=BoomWorker())
    listed = await undeliverable_entries(session)
    assert correlation_id in [e.correlation_id for e in listed]

    receipt = await replay(session, correlation_id)
    await session.commit()
    assert receipt.prior_entries == {"undeliverable": 1}
    assert await drain() == 1  # the fault has cleared; default worker persists

    row = (
        await session.execute(
            select(BloodPressureReading).where(
                BloodPressureReading.correlation_id == correlation_id
            )
        )
    ).scalar_one()
    assert row.user_id == user_id
    assert await _entry_states(session, correlation_id) == ["undeliverable", "done"]
    # Recovery sheds the stale entry from the worklist (the row itself is history):
    # 's depth signal must fall when work recovers, not grow monotonically.
    assert correlation_id not in [e.correlation_id for e in await undeliverable_entries(session)]


@pytest.mark.anyio
async def test_replay_of_completed_work_writes_nothing(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """with replaying already-persisted work completes cleanly as a no-op."""
    device = "SM-OPS-IDEMPOTENT"
    await _register(session, email="ops-idempotent@wanda.test", device_id=device)
    correlation_id = _ingest(client, {**BP_PAYLOAD, "device_id": device, "reading_id": 77102})
    assert await drain() == 1

    receipt = await replay(session, correlation_id)
    await session.commit()
    assert receipt.prior_entries == {"done": 1}
    assert await drain() == 1  # the replay delivery — handled, deduped

    rows = (
        await session.execute(
            select(func.count())
            .select_from(BloodPressureReading)
            .where(BloodPressureReading.correlation_id == correlation_id)
        )
    ).scalar_one()
    assert rows == 1
    assert await _entry_states(session, correlation_id) == ["done", "done"]


@pytest.mark.anyio
async def test_replay_unknown_correlation_raises(session: AsyncSession) -> None:
    """Replaying an unknown correlation id raises LookupError, never a silent no-op."""
    with pytest.raises(LookupError, match="ops-never-captured"):
        await replay(session, "ops-never-captured")


@pytest.mark.anyio
async def test_register_then_replay_resolves_quarantine(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """'s manual resolution flow end to end: quarantined, operator
    registers the device, replays — reading persists and the open record resolves."""
    device = "SM-OPS-QUARANTINE"
    correlation_id = _ingest(client, {**BP_PAYLOAD, "device_id": device, "reading_id": 77103})
    assert await drain() == 1  # no registration → quarantined, acked

    worklist = await quarantined_readings(session)
    assert (correlation_id, device) in [(q.correlation_id, q.device_id) for q in worklist]

    user_id = await _register(session, email="ops-quarantine@wanda.test", device_id=device)
    await replay(session, correlation_id)
    await session.commit()
    assert await drain() == 1

    row = (
        await session.execute(
            select(BloodPressureReading).where(
                BloodPressureReading.correlation_id == correlation_id
            )
        )
    ).scalar_one()
    assert row.user_id == user_id
    record = (
        await session.execute(
            select(QuarantinedReading).where(QuarantinedReading.correlation_id == correlation_id)
        )
    ).scalar_one()
    assert record.state == "resolved"
    assert correlation_id not in [q.correlation_id for q in await quarantined_readings(session)]


@pytest.mark.anyio
async def test_undeliverable_listing_is_identifiers_only(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """The listing carries the correlation id, counts, and the error *shape*
    never the offending values; done entries stay out of it."""
    malformed = {**BP_PAYLOAD, "reading_id": 77104, "systolic_mmhg": "128.SENTINEL.mmHg"}
    correlation_id = _ingest(client, malformed)
    await drain()  # validation failure walks the whole retry ladder (delay 0)

    listed = {e.correlation_id: e for e in await undeliverable_entries(session)}
    entry = listed[correlation_id]
    assert entry.receive_count == MAX_RECEIVE_COUNT
    assert entry.last_error is not None
    assert "systolic_mmhg" in entry.last_error  # the field name (the shape)...
    assert "SENTINEL" not in entry.last_error  # ...never the supplied value
    # The listing is per-entry and state-scoped: nothing done/ready/in_flight leaks in.
    assert all(e.state == "undeliverable" for e in listed.values())


@pytest.mark.anyio
async def test_in_flight_listing_surfaces_stuck_claims(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """The committed-claim policy's documented crash mode: a claim that never reached an
    outcome is visible to the operator (consumer.py's promise to)."""
    from app.services.reading_queue import get_reading_queue

    correlation_id = _ingest(
        client, {**BP_PAYLOAD, "device_id": "SM-OPS-STUCK", "reading_id": 77105}
    )
    entry = (
        await session.execute(
            select(ReadingQueueEntry).where(ReadingQueueEntry.correlation_id == correlation_id)
        )
    ).scalar_one()
    claimed = await get_reading_queue().dequeue(session)
    assert claimed is not None and claimed.id == entry.id
    await session.commit()  # the hard kill happens here: claim committed, no outcome ever

    stuck = [e.correlation_id for e in await in_flight_entries(session)]
    assert correlation_id in stuck
    # Recovery is the standard handle: replay joins a fresh entry to the history.
    receipt = await replay(session, correlation_id)
    await session.commit()
    assert receipt.prior_entries == {"in_flight": 1}
    # Once the replay succeeds (here: quarantined — the device is unregistered),
    # the stuck claim leaves the worklist; the in_flight row itself survives.
    assert await drain() == 1
    assert correlation_id not in [e.correlation_id for e in await in_flight_entries(session)]
    assert "in_flight" in await _entry_states(session, correlation_id)


@pytest.mark.anyio
async def test_discard_closes_the_quarantine_worklist(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """Discarding a quarantined reading closes it off the operator worklist."""
    correlation_id = _ingest(
        client, {**BP_PAYLOAD, "device_id": "SM-OPS-DISCARD", "reading_id": 77106}
    )
    assert await drain() == 1  # unregistered → quarantined

    quarantine_id = await discard_quarantined(session, correlation_id)
    await session.commit()
    record = (
        await session.execute(
            select(QuarantinedReading).where(QuarantinedReading.id == quarantine_id)
        )
    ).scalar_one()
    assert record.state == "discarded"
    assert correlation_id not in [q.correlation_id for q in await quarantined_readings(session)]
    with pytest.raises(LookupError):  # already closed — nothing open to discard
        await discard_quarantined(session, correlation_id)

    # "Never-to-be-processed" holds against replay too: even after the device is
    # registered, a discarded correlation is refused (discard is final in Phase 1).
    await _register(session, email="ops-discard@wanda.test", device_id="SM-OPS-DISCARD")
    with pytest.raises(ReplayRefused, match="discarded"):
        await replay(session, correlation_id)


@pytest.mark.anyio
async def test_discard_defeats_a_replay_enqueued_before_it(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """Epic-gate finding: replay first (record still open — allowed), discard second.
    The already-enqueued replay message must die with the record. Worst ending guarded
    here: the device gains a registration, and the suppressed reading must still never
    reach the member's record."""
    device = "SM-OPS-DISCARD-RACE"
    correlation_id = _ingest(client, {**BP_PAYLOAD, "device_id": device, "reading_id": 77107})
    assert await drain() == 1  # unregistered → quarantined, acked

    await replay(session, correlation_id)  # allowed: the record is open at this point
    await session.commit()
    await discard_quarantined(session, correlation_id)
    await session.commit()
    await _register(session, email="ops-discard-race@wanda.test", device_id=device)

    await drain()  # whatever survives in the queue must drop, never attribute

    async def persisted_count() -> int:
        return (
            await session.execute(
                select(func.count())
                .select_from(BloodPressureReading)
                .where(BloodPressureReading.correlation_id == correlation_id)
            )
        ).scalar_one()

    assert await persisted_count() == 0  # the operator's suppression holds
    states = await _entry_states(session, correlation_id)
    assert "ready" not in states and "in_flight" not in states  # nothing left to fire later

    # The racing half: an entry that slips past discard's cancel (claimed mid-race) still
    # dies at the Processor's own discard check — simulated by enqueueing directly past
    # the replay guard.
    raw_ref = await get_raw_store().ref_for_correlation(session, correlation_id)
    assert raw_ref is not None
    await get_reading_queue().enqueue(session, raw_ref=raw_ref, correlation_id=correlation_id)
    await session.commit()
    assert await drain() == 1  # handled: acked as an explicit drop
    assert await persisted_count() == 0
    assert "ready" not in await _entry_states(session, correlation_id)


@pytest.mark.anyio
async def test_dev_mirrors_walk_the_discard_scenario(
    drained: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bruno scenario 4's spine over the dev mirrors: quarantine → replay (allowed) →
    discard → the pending replay drops → worklist clear, later replay refused (409)."""
    from app.config import settings
    from app.main import create_app

    # monkeypatch restores the pre-test value; a hardcoded reset can mask pollution.
    monkeypatch.setattr(settings, "dev_mode", True)
    dev = TestClient(create_app())
    correlation_id = _ingest(
        dev, {**BP_PAYLOAD, "device_id": "SM-OPS-DEV-DISCARD", "reading_id": 77109}
    )
    assert dev.post("/v1/dev/pipeline/run").json()["processed"] == 1  # → quarantined
    assert correlation_id in [q["correlation_id"] for q in dev.get("/v1/dev/quarantine").json()]
    replayed = dev.post("/v1/dev/replay", json={"correlation_id": correlation_id})
    assert replayed.status_code == 202  # the record is still open — replay allowed

    discarded = dev.post("/v1/dev/quarantine-discard", json={"correlation_id": correlation_id})
    assert discarded.status_code == 200
    assert discarded.json()["correlation_id"] == correlation_id

    dev.post("/v1/dev/pipeline/run")  # anything surviving for the correlation drops
    assert correlation_id not in [q["correlation_id"] for q in dev.get("/v1/dev/quarantine").json()]
    refused = dev.post("/v1/dev/replay", json={"correlation_id": correlation_id})
    assert refused.status_code == 409  # discard is final
    again = dev.post("/v1/dev/quarantine-discard", json={"correlation_id": correlation_id})
    assert again.status_code == 404  # nothing open to discard twice


@pytest.mark.anyio
async def test_discarded_correlation_never_requarantines(
    client: TestClient, session: AsyncSession, drained: None
) -> None:
    """The other ending of the same sequence: device still unregistered — the replayed
    message must NOT resurrect an open worklist record whose replay is refused forever."""
    device = "SM-OPS-DISCARD-DEAD"
    correlation_id = _ingest(client, {**BP_PAYLOAD, "device_id": device, "reading_id": 77108})
    assert await drain() == 1

    await replay(session, correlation_id)
    await session.commit()
    await discard_quarantined(session, correlation_id)
    await session.commit()

    await drain()

    record_states = (
        (
            await session.execute(
                select(QuarantinedReading.state).where(
                    QuarantinedReading.correlation_id == correlation_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert record_states == ["discarded"]  # exactly the one record, still discarded
    assert correlation_id not in [q.correlation_id for q in await quarantined_readings(session)]
