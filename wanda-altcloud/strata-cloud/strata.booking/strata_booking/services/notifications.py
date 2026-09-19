"""Notification seam: one protocol, swappable delivery.

Phase 1 ships :class:`LocalStoreSender`, which records every send in the ``notifications``
table instead of delivering it — viewable via ``GET /v1/dev/notifications`` so reminder and
suggested-alternatives emails can be demonstrated locally. Phase 2 swaps in SES/SNS senders
behind the same protocol; no PHI in payloads either way.
"""

from typing import Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Notification

Channel = Literal["email", "push"]
RecipientKind = Literal["member", "coach"]


class NotificationSender(Protocol):
    async def send(
        self,
        session: AsyncSession,
        *,
        channel: Channel,
        recipient_kind: RecipientKind,
        recipient_id: str,
        notification_type: str,
        payload: dict[str, object],
    ) -> None: ...


class LocalStoreSender:
    """Phase 1 sender: persist the notification in the current transaction; deliver nothing."""

    async def send(
        self,
        session: AsyncSession,
        *,
        channel: Channel,
        recipient_kind: RecipientKind,
        recipient_id: str,
        notification_type: str,
        payload: dict[str, object],
    ) -> None:
        session.add(
            Notification(
                channel=channel,
                recipient_kind=recipient_kind,
                recipient_id=recipient_id,
                notification_type=notification_type,
                payload=payload,
            )
        )


_sender: NotificationSender = LocalStoreSender()


def get_sender() -> NotificationSender:
    """The configured sender (module singleton; Phase 2 selects by settings)."""
    return _sender
