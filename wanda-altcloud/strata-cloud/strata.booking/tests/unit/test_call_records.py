"""Unit tests for the call-record seam's window logic (feeds no-show detection)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import CallRecord

from strata_booking.services.call_records import get_provider

CALL_AT = datetime(2026, 7, 6, 14, 0, tzinfo=UTC)


@pytest.mark.anyio
async def test_call_inside_window_is_found(session: AsyncSession) -> None:
    """A call record inside the detection window is found for its coach/member pair."""
    session.add(CallRecord(coach_id="c-9", member_id="m-9", initiated_at_utc=CALL_AT))
    await session.commit()
    assert await get_provider().call_initiated(
        session,
        coach_id="c-9",
        member_id="m-9",
        window_start=CALL_AT - timedelta(minutes=5),
        window_end=CALL_AT + timedelta(minutes=15),
    )


@pytest.mark.anyio
async def test_call_outside_window_or_other_pair_is_not_found(session: AsyncSession) -> None:
    """Calls outside the window or for a different pair do not count."""
    session.add(CallRecord(coach_id="c-9", member_id="m-8", initiated_at_utc=CALL_AT))
    await session.commit()
    provider = get_provider()
    # Wrong member.
    assert not await provider.call_initiated(
        session,
        coach_id="c-9",
        member_id="m-7",
        window_start=CALL_AT - timedelta(minutes=5),
        window_end=CALL_AT + timedelta(minutes=15),
    )
    # Right pair, window that ends before the call.
    assert not await provider.call_initiated(
        session,
        coach_id="c-9",
        member_id="m-8",
        window_start=CALL_AT - timedelta(hours=2),
        window_end=CALL_AT - timedelta(hours=1),
    )
