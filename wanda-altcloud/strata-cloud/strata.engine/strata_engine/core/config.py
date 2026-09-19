"""Application configuration, loaded from the environment (and an optional .env)."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STRATA_", env_file=".env", extra="ignore")

    app_name: str = "Strata API"

    # Observability. The instrumentation is identical everywhere; only these knobs
    # change per environment. Local-safe defaults: pretty console logs, tracing off (so `inv dev`
    # needs no extra infra). AWS sets log_format=json + otel_traces_exporter=otlp via env; opt into
    # local traces with otel_traces_exporter=otlp + the local Jaeger endpoint (see .env.example).
    service_name: str = "strata"
    environment: str = "local"
    log_format: Literal["console", "json"] = "console"
    otel_traces_exporter: Literal["none", "console", "otlp"] = "none"
    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_sampler_ratio: float = 1.0


settings = Settings()
