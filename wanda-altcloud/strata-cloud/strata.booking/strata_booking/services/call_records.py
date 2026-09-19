"""Call-record seam for no-show detection.

Phase 1 ships :class:`LocalTableProvider`, which answers "was a call initiated in this window?"
from the ``call_records`` table (populated via ``POST /v1/dev/call-records`` to simulate a
Twilio call). Phase 2 swaps in a Twilio-backed provider behind the same protocol.
"""

from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import CallRecord


class CallRecordProvider(Protocol):
    async def call_initiated(
        self,
        session: AsyncSession,
        *,
        coach_id: str,
        member_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> bool: ...


class LocalTableProvider:
    async def call_initiated(
        self,
        session: AsyncSession,
        *,
        coach_id: str,
        member_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> bool:
        stmt = (
            select(CallRecord.id)
            .where(
                CallRecord.coach_id == coach_id,
                CallRecord.member_id == member_id,
                CallRecord.initiated_at_utc >= window_start,
                CallRecord.initiated_at_utc <= window_end,
            )
            .limit(1)
        )
        return (await session.execute(stmt)).first() is not None


_provider: CallRecordProvider = LocalTableProvider()


def get_provider() -> CallRecordProvider:
    """The configured provider (module singleton; Phase 2 selects by settings)."""
    return _provider
