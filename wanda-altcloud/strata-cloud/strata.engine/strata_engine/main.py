"""Strata FastAPI application factory.

The reference-data domains (medications, procedures, diagnoses) were extracted to
``../strata.terminology`` — this service is a shell (health + the
observability baseline) whose future is a separate post-housekeeping decision. A new
``/v1`` router tree returns here the moment the service regains a purpose.
"""

from fastapi import FastAPI

from strata_engine.api import health
from strata_engine.api.middleware import CorrelationIdMiddleware
from strata_engine.core.config import settings
from strata_engine.core.observability import configure_logging, configure_tracing


def create_app() -> FastAPI:
    configure_logging(settings)
    app = FastAPI(title=settings.app_name, version="0.1.0", summary="Strata.Engine shell.")
    app.add_middleware(CorrelationIdMiddleware)
    # Operational endpoints stay unversioned.
    app.include_router(health.router)
    # Request spans — must be set up before the app serves traffic; no-op when tracing is off.
    configure_tracing(app, settings)
    return app


app = create_app()
