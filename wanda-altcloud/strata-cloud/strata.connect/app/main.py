"""FastAPI application factory — baseline-wired ( versioning, observability)."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI

from app.api import dev, health, readings, registrations
from app.api.middleware import CorrelationIdMiddleware
from app.config import settings
from app.db import init_engine
from app.observability import configure_logging, configure_tracing, instrument_engine
from app.services.consumer import run_loop

_log = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Engine created at startup so the URL is read from settings at runtime (see app/db.py).
    engine = init_engine()
    # SQL query spans — done here because it needs the live engine; a no-op when tracing is off.
    instrument_engine(engine, settings)
    # Optional in-process consumer loop (booking's jobs pattern); 0 = off.
    loop_task = (
        asyncio.create_task(run_loop(settings.pipeline_interval_seconds))
        if settings.pipeline_interval_seconds > 0
        else None
    )
    try:
        yield
    finally:
        try:
            if loop_task is not None:
                loop_task.cancel()
                try:
                    await loop_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # A task that died before shutdown must not mask its cause as a
                    # teardown error — log it clearly, and never skip engine disposal.
                    _log.exception("pipeline consumer task had already died")
        finally:
            await engine.dispose()


def create_app() -> FastAPI:
    configure_logging(settings)
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=_lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    # Operational endpoints stay unversioned; APIs are versioned by URI major.
    app.include_router(health.router)
    v1 = APIRouter(prefix="/v1")
    v1.include_router(readings.router)
    v1.include_router(registrations.router)
    if settings.dev_mode:
        # Local conveniences only — the flag is never set in AWS (booking's dev pattern).
        v1.include_router(dev.router)
    app.include_router(v1)
    # Request spans — set up before serving; no-op when tracing is off.
    configure_tracing(app, settings)
    return app


app = create_app()
