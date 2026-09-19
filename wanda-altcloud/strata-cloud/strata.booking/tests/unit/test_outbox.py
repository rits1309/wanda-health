"""Unit tests for the transactional outbox seam."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import OutboxEvent

from strata_booking.core import clock as clock_module
from strata_booking.services.outbox import emit

PINNED = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_emit_writes_event_in_transaction(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """emit writes the outbox event inside the caller's transaction."""
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: PINNED)
    emit(session, "OutboxSelfTestEvent", {"appointment_id": "a-1", "acting_user_id": "m-1"})
    await session.commit()

    row = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "OutboxSelfTestEvent")
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.payload == {"appointment_id": "a-1", "acting_user_id": "m-1"}
    assert row.occurred_at == PINNED
    assert row.published_at is None  # Phase 1: no relay to Elevate yet


@pytest.mark.anyio
async def test_emit_rolls_back_with_the_transaction(session: AsyncSession) -> None:
    """A rolled-back transaction takes its outbox event with it."""
    emit(session, "RolledBackEvent", {"x": 1})
    await session.rollback()
    count = (
        await session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "RolledBackEvent")
        )
    ).first()
    assert count is None
