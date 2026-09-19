"""The ingest edge: accept, capture verbatim, enqueue, acknowledge.

In Phase 2 this exact logic becomes the Lambda body behind API Gateway; the endpoint
remains for local dev. No canonical validation happens here — the edge's only
job is durable capture; the Processor validates downstream, so a
malformed *reading* is still captured and replayable. Any well-formed JSON value is
accepted (the provider contract is unconfirmed until Phase 2); only syntactically
invalid JSON is rejected before capture — a JSONB store cannot hold it, and the
Phase 2 S3 edge removes even that restriction.
"""

import structlog
from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.db import new_id

from app.api.auth import require_edge_secret
from app.db import get_session
from app.schemas.ingest import IngestAccepted
from app.services.raw_store import RawStore, get_raw_store
from app.services.reading_queue import ReadingQueue, get_reading_queue

router = APIRouter(
    prefix="/readings", tags=["readings"], dependencies=[Depends(require_edge_secret)]
)
_log = structlog.get_logger(__name__)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestAccepted,
    summary="Ingest a SmartMeter reading",
)
async def ingest_reading(
    payload: object = Body(),
    session: AsyncSession = Depends(get_session),
    store: RawStore = Depends(get_raw_store),
    queue: ReadingQueue = Depends(get_reading_queue),
) -> IngestAccepted:
    """Capture the raw payload and enqueue its reference in one transaction."""
    correlation_id = new_id()  # assigned at ingest, carried everywhere
    # Bind it so every remaining log line for this request (incl. the middleware's access
    # log) carries the reading's correlation id, not just the transport request id.
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
    raw_ref = await store.put(session, payload=payload, correlation_id=correlation_id)
    await queue.enqueue(session, raw_ref=raw_ref, correlation_id=correlation_id)
    await session.commit()
    # Identifiers only — clinical values never reach the logs.
    _log.info("reading captured", raw_ref=raw_ref)
    return IngestAccepted(correlation_id=correlation_id)
