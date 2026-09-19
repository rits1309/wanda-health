"""Strata.Engine.Auth FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from strata_engine_auth.api import auth, health
from strata_engine_auth.api.middleware import CorrelationIdMiddleware
from strata_engine_auth.core.config import settings
from strata_engine_auth.core.observability import (
    configure_logging,
    configure_tracing,
    instrument_engine,
)
from strata_engine_auth.db.session import init_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the database engine on startup, dispose it on shutdown."""
    engine = init_engine()
    # SQL query spans — needs the live engine; a no-op when tracing is off.
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
        summary="Savanna authentication: the single platform Cognito user pool.",
        lifespan=lifespan,
    )
    app.add_middleware(CorrelationIdMiddleware)
    # Browser clients (Summit) call this API directly; origins from Settings.
    origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"]
    )
    # Operational endpoints stay unversioned; the auth API is versioned by URI
    # major. A new major would be a second parallel router tree here.
    app.include_router(health.router)
    v1 = APIRouter(prefix="/v1")
    v1.include_router(auth.router)
    app.include_router(v1)
    # Request spans — must be set up before the app serves traffic; no-op when tracing is off.
    configure_tracing(app, settings)
    return app


app = create_app()
