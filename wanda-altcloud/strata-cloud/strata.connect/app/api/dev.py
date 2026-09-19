"""Dev-only operations (mounted only when STRATA_DEV_MODE=true — never AWS).

Booking's dev-router pattern: local conveniences behind an explicit opt-in flag. The
pipeline endpoint runs the consumer once ("a dev endpoint for single-shot processing");
the rest are HTTP mirrors of the operator tooling
(``app/services/operations.py``) so the bruno scenario walkthroughs can list and replay
without leaving the collection. The `inv` tasks remain the operator surface proper.
"""

from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import operations
from app.services.consumer import drain

router = APIRouter(prefix="/dev", tags=["dev"])
_log = structlog.get_logger(__name__)


class PipelineRunResult(BaseModel):
    processed: int = Field(description="Messages handled this run (acked or failed).")


@router.post("/pipeline/run", summary="Run the pipeline consumer once (drain the queue)")
async def run_pipeline_once() -> PipelineRunResult:
    processed = await drain()
    _log.info("dev pipeline run", processed=processed)
    return PipelineRunResult(processed=processed)


class QueueEntryOut(BaseModel):
    """Identifiers only; ``last_error`` is an error shape by construction."""

    id: str
    correlation_id: str
    state: str
    receive_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class QueueListing(BaseModel):
    undeliverable: list[QueueEntryOut]
    in_flight: list[QueueEntryOut] = Field(
        description="Possibly stuck claims — a live consumer holds one only for milliseconds."
    )


@router.get("/queue/undeliverable", summary="List undeliverable messages and stuck claims")
async def list_undeliverable(session: AsyncSession = Depends(get_session)) -> QueueListing:
    return QueueListing(
        undeliverable=[
            QueueEntryOut(**vars(e)) for e in await operations.undeliverable_entries(session)
        ],
        in_flight=[QueueEntryOut(**vars(e)) for e in await operations.in_flight_entries(session)],
    )


class QuarantineOut(BaseModel):
    id: str
    correlation_id: str
    device_id: str
    reading_type: str
    state: str
    created_at: datetime


@router.get("/quarantine", summary="List open quarantine records (register + replay to resolve)")
async def list_quarantine(session: AsyncSession = Depends(get_session)) -> list[QuarantineOut]:
    return [QuarantineOut(**vars(q)) for q in await operations.quarantined_readings(session)]


class ReplayRequest(BaseModel):
    correlation_id: str


class ReplayResult(BaseModel):
    entry_id: str
    correlation_id: str
    prior_entries: dict[str, int] = Field(
        description="The correlation's existing queue entries by state — what the replay joins."
    )


@router.post(
    "/replay",
    status_code=202,
    summary="Re-enqueue a captured payload under its original correlation id ",
)
async def replay_reading(
    body: ReplayRequest, session: AsyncSession = Depends(get_session)
) -> ReplayResult:
    try:
        receipt = await operations.replay(session, body.correlation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except operations.ReplayRefused as exc:
        # The discarded exit stays closed (never-to-be-processed); 409, not 404 —
        # the payload exists, replaying it is what's refused.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return ReplayResult(**vars(receipt))


class DiscardRequest(BaseModel):
    correlation_id: str


class DiscardResult(BaseModel):
    quarantine_id: str
    correlation_id: str


@router.post(
    "/quarantine-discard",
    summary="Close an open quarantine record as never-to-be-processed (final in Phase 1)",
)
async def discard_quarantined_reading(
    body: DiscardRequest, session: AsyncSession = Depends(get_session)
) -> DiscardResult:
    try:
        quarantine_id = await operations.discard_quarantined(session, body.correlation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return DiscardResult(quarantine_id=quarantine_id, correlation_id=body.correlation_id)
