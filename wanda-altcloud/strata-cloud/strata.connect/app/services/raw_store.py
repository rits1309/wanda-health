"""The RawStore seam: payload in, opaque ``raw_ref`` out.

Phase 1 backs it with the kernel's ``raw_readings`` table (Postgres over filesystem
one-container dev, queryable). Phase 2 swaps in S3, where ``raw_ref`` becomes the object
reference; callers hold only the seam, so nothing above this module changes.

A consequence worth knowing: JSONB preserves the payload's *JSON semantics* (values,
structure), not its exact bytes — key order, whitespace, duplicate keys, and number lexemes
are normalised. Phase 2's S3 store is byte-exact; anything byte-sensitive (e.g. a provider
signature over the raw body) cannot be verified from Phase 1 storage.
"""

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.device_readings import RawReading

from app.clock import clock


class RawStore(Protocol):
    """What the ingest edge, the processor, and the replay tooling depend on."""

    async def put(self, session: AsyncSession, *, payload: Any, correlation_id: str) -> str: ...

    async def get(self, session: AsyncSession, raw_ref: str) -> Any: ...

    async def ref_for_correlation(
        self, session: AsyncSession, correlation_id: str
    ) -> str | None: ...


class PostgresRawStore:
    """Phase 1 implementation: verbatim JSONB rows in ``raw_readings``."""

    async def put(self, session: AsyncSession, *, payload: Any, correlation_id: str) -> str:
        """Persist any JSON value unmodified; the row id is the ``raw_ref``."""
        raw = RawReading(payload=payload, correlation_id=correlation_id, received_at=clock.now())
        session.add(raw)
        await session.flush()  # assign the id inside the caller's transaction
        return raw.id

    async def get(self, session: AsyncSession, raw_ref: str) -> Any:
        """Fetch a payload by reference; raises ``LookupError`` on a dangling ref."""
        row = (
            await session.execute(select(RawReading).where(RawReading.id == raw_ref))
        ).scalar_one_or_none()
        if row is None:
            raise LookupError(f"no raw reading stored under ref {raw_ref!r}")
        return row.payload

    async def ref_for_correlation(self, session: AsyncSession, correlation_id: str) -> str | None:
        """The reference captured under a correlation id, or None — replay's lookup
        (replays *from the raw store*, so the seam must answer this in Phase 2
        too: the S3 implementation keys or indexes objects by correlation id)."""
        return (
            await session.execute(
                select(RawReading.id).where(RawReading.correlation_id == correlation_id)
            )
        ).scalar_one_or_none()


_store = PostgresRawStore()


def get_raw_store() -> RawStore:
    """FastAPI dependency — routes depend on the seam, not the implementation."""
    return _store
