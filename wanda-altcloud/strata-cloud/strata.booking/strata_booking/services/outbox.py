"""The transactional outbox — the dual-output seam.

Every significant business operation calls :func:`emit` **inside the same session/transaction**
as its state change, so the Strata.Core-shaped write and the Elevate bronze event commit or
roll back together. Phase 1 writes the event to ``outbox_events`` only; the relay to Elevate is
a later phase, which is why ``published_at`` stays
NULL here.

Every payload must carry the acting user (``acting_user_id``) and, where an admin acts for a
coach, ``on_behalf_of_coach_id``.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import OutboxEvent

from strata_booking.core.clock import clock


def emit(session: AsyncSession, event_type: str, payload: dict[str, object]) -> None:
    """Queue a business event in the current transaction (flushed/committed by the caller)."""
    session.add(OutboxEvent(event_type=event_type, payload=payload, occurred_at=clock.now()))
