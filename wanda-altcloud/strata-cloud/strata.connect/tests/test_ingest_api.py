"""The ingest edge (b): capture verbatim + enqueue, auth rejection."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.device_readings import RawReading, ReadingQueueEntry

from tests.conftest import EDGE_SECRET
from tests.sample_payloads import BP_PAYLOAD, WEIGHT_PAYLOAD

AUTH = {"X-API-Key": EDGE_SECRET}


async def _row_counts(session: AsyncSession) -> tuple[int, int]:
    raws = (await session.execute(select(func.count()).select_from(RawReading))).scalar_one()
    queued = (
        await session.execute(select(func.count()).select_from(ReadingQueueEntry))
    ).scalar_one()
    return raws, queued


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [BP_PAYLOAD, WEIGHT_PAYLOAD])
async def test_reading_accepted_and_captured(
    client: TestClient, session: AsyncSession, payload: dict[str, object]
) -> None:
    """202 + verbatim raw row + a ready reference message, one correlation id."""
    response = client.post("/v1/readings", json=payload, headers=AUTH)

    assert response.status_code == 202
    correlation_id = response.json()["correlation_id"]

    raw = (
        await session.execute(select(RawReading).where(RawReading.correlation_id == correlation_id))
    ).scalar_one()
    assert raw.payload == payload  # unmodified

    entry = (
        await session.execute(
            select(ReadingQueueEntry).where(ReadingQueueEntry.correlation_id == correlation_id)
        )
    ).scalar_one()
    assert entry.raw_ref == raw.id
    assert entry.state == "ready"
    assert entry.receive_count == 0


@pytest.mark.anyio
async def test_malformed_payload_still_captured(client: TestClient, session: AsyncSession) -> None:
    """The edge does no canonical validation — durable capture first; the
    Processor rejects downstream and the payload stays replayable."""
    response = client.post("/v1/readings", json={"not": "a reading"}, headers=AUTH)

    assert response.status_code == 202
    correlation_id = response.json()["correlation_id"]
    raw = (
        await session.execute(select(RawReading).where(RawReading.correlation_id == correlation_id))
    ).scalar_one()
    assert raw.payload == {"not": "a reading"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-API-Key": "wrong-secret"},
        # A crafted non-ASCII key (any latin-1 byte is legal on the wire) must yield a
        # clean 401 — never a TypeError out of the secret comparison.
        {b"X-API-Key": "clé-sécrète".encode("latin-1")},
    ],
)
async def test_unauthenticated_delivery_rejected(
    client: TestClient, session: AsyncSession, headers: dict[str, str] | dict[bytes, bytes]
) -> None:
    """authentication error, and nothing captured, nothing enqueued."""
    before = await _row_counts(session)

    response = client.post("/v1/readings", json=BP_PAYLOAD, headers=headers)

    assert response.status_code == 401
    assert await _row_counts(session) == before


@pytest.mark.anyio
async def test_non_object_json_still_captured(client: TestClient, session: AsyncSession) -> None:
    """has no shape exemption: an authenticated non-object delivery (the contract is
    unconfirmed until Phase 2) must be captured, not silently lost to a 422."""
    response = client.post("/v1/readings", json=[1, 2, 3], headers=AUTH)

    assert response.status_code == 202
    correlation_id = response.json()["correlation_id"]
    raw = (
        await session.execute(select(RawReading).where(RawReading.correlation_id == correlation_id))
    ).scalar_one()
    assert raw.payload == [1, 2, 3]
