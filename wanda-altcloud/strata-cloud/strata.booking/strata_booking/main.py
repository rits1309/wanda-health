"""strata.booking FastAPI application factory — baseline-wired."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from strata_booking.api import (
    admin,
    alternatives,
    availability,
    booking,
    calendar,
    dev,
    health,
    members,
    policies,
    reschedule_links,
    unavailability,
)
from strata_booking.api.middleware import CorrelationIdMiddleware, NulByteRejectionMiddleware
from strata_booking.core.config import settings
from strata_booking.core.observability import (
    configure_logging,
    configure_tracing,
    instrument_engine,
)
from strata_booking.db.session import init_engine, session_factory
from strata_booking.services import jobs

_log = structlog.get_logger("jobs")


async def _jobs_loop(interval_seconds: int) -> None:
    """Optional in-process jobs loop — hands-free local running only."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            async with session_factory()() as session:
                await jobs.run_all(session)
        except Exception:  # keep the loop alive; the next tick retries (jobs are idempotent)
            _log.exception("jobs_loop_run_failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database engine on startup, dispose it on shutdown."""
    engine = init_engine()
    # SQL query spans — done here because it needs the live engine; a no-op when tracing is off.
    instrument_engine(engine, settings)
    loop_task = (
        asyncio.create_task(_jobs_loop(settings.jobs_interval_seconds))
        if settings.jobs_interval_seconds > 0
        else None
    )
    try:
        yield
    finally:
        if loop_task is not None:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
        await engine.dispose()


def create_app() -> FastAPI:
    configure_logging(settings)
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        summary="Savanna coaching appointment booking service.",
        lifespan=lifespan,
    )
    # Added first so the correlation-ID middleware wraps it and its 400s are still logged.
    app.add_middleware(NulByteRejectionMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    if settings.cors_origins:
        # Local dev only (Summit's Vite dev server); empty by default so AWS never enables it.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    # Operational endpoints stay unversioned; APIs are versioned by URI major.
    app.include_router(health.router)
    v1 = APIRouter(prefix="/v1")
    v1.include_router(availability.router)
    v1.include_router(unavailability.blocks_router)
    v1.include_router(unavailability.recurring_router)
    v1.include_router(calendar.router)
    v1.include_router(members.router)
    v1.include_router(booking.slots_router)
    v1.include_router(booking.appointments_router)
    v1.include_router(policies.router)
    v1.include_router(alternatives.router)
    v1.include_router(reschedule_links.router)
    v1.include_router(admin.router)
    if settings.dev_mode:
        v1.include_router(dev.router)
    app.include_router(v1)
    # Request spans — set up before serving; no-op when tracing is off.
    configure_tracing(app, settings)
    return app


app = create_app()
