"""Unit tests for the notification seam's Phase 1 LocalStoreSender."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Notification

from strata_booking.services.notifications import LocalStoreSender, get_sender


@pytest.mark.anyio
async def test_local_sender_records_instead_of_delivering(session: AsyncSession) -> None:
    """The local sender records notifications instead of delivering them."""
    await get_sender().send(
        session,
        channel="email",
        recipient_kind="member",
        recipient_id="m-notif-test",  # unique recipient: isolates from other suites' sends
        notification_type="appointment_confirmed",
        payload={"appointment_id": "a-1"},
    )
    await session.commit()

    row = (
        (
            await session.execute(
                select(Notification).where(Notification.recipient_id == "m-notif-test")
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    assert row.channel == "email"
    assert row.notification_type == "appointment_confirmed"
    assert row.payload == {"appointment_id": "a-1"}


def test_default_sender_is_the_local_store() -> None:
    """The default notification sender is the local store (Phase 1 posture)."""
    assert isinstance(get_sender(), LocalStoreSender)
