"""FastAPI application factory — baseline-wired ( versioning, observability).

Every ``/v1`` router mounts behind the verification seam (any authenticated user —
core/security.py); operational endpoints (``/health``) stay unversioned and open.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from strata_terminology.api import diagnoses, health, medications, procedures
from strata_terminology.api.middleware import CorrelationIdMiddleware
from strata_terminology.core.config import settings
from strata_terminology.core.observability import (
    configure_logging,
    configure_tracing,
    instrument_engine,
)
from strata_terminology.core.security import current_principal
from strata_terminology.db.session import init_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database engine on startup, dispose it on shutdown."""
    engine = init_engine()
    # SQL query spans — done here because it needs the live engine; a no-op when tracing is off.
    instrument_engine(engine, settings)
    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    configure_logging(settings)
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        summary="Savanna's clinical terminology (reference-data) service.",
        lifespan=lifespan,
    )
    app.add_middleware(CorrelationIdMiddleware)
    # CORS for the local Vite dev server (Summit); off unless configured.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    # Operational endpoints stay unversioned; reference-data APIs are versioned by URI
    # major. Every /v1 route requires a valid token (any authenticated user).
    app.include_router(health.router)
    v1 = APIRouter(prefix="/v1", dependencies=[Depends(current_principal)])
    v1.include_router(medications.router)
    v1.include_router(procedures.router)
    v1.include_router(diagnoses.router)
    app.include_router(v1)
    # Request spans — set up before serving; no-op when tracing is off.
    configure_tracing(app, settings)
    return app


app = create_app()
