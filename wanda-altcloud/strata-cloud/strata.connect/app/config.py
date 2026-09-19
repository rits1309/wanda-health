"""Settings, loaded from STRATA_-prefixed env vars. Observability knobs per."""

from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STRATA_", env_file=".env", extra="ignore")

    app_name: str = "strata.connect"
    service_name: str = "strata.connect"
    environment: str = "local"

    # Database (required — the app fails fast at startup if unset; see .env.example).
    # Deliberately NO db_echo switch: echoing bound parameters would write
    # raw clinical payloads to the logs. Debug SQL against a local psql instead.
    database_url: str

    # Edge auth: a static shared secret locally, checked on every ingest and
    # registration request (X-API-Key). Required — no secret, no startup (fail closed).
    # Phase 2 replaces this with the provider API key at the real edge.
    edge_shared_secret: str

    # Pipeline consumer. Standalone runner cadence (`inv pipeline-run`):
    pipeline_poll_seconds: float = 1.0
    # Retry visibility delay: a failed message becomes claimable again
    # only after this many seconds — SQS's visibility-timeout property, without which a
    # transient fault would burn all three retries in milliseconds and dead-letter
    # readings a few seconds' patience would have saved.
    pipeline_retry_delay_seconds: float = 30.0
    # Optional in-process loop, booking's jobs pattern: >0 runs the consumer inside the
    # API process every N seconds; 0 (default) = off — use the runner or the dev endpoint.
    pipeline_interval_seconds: int = 0

    # Dev-mode seam (never enabled outside local): mounts /v1/dev/* (single-shot pipeline run).
    dev_mode: bool = False

    log_format: Literal["console", "json"] = "console"
    otel_traces_exporter: Literal["none", "console", "otlp"] = "none"
    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_sampler_ratio: float = 1.0

    @model_validator(mode="after")
    def _dev_mode_requires_explicit_local(self) -> Self:
        """Fail closed (booking's doctrine): dev mode only starts when
        STRATA_ENVIRONMENT is EXPLICITLY "local" (env var or .env) — the Settings default
        never counts, so a stray STRATA_DEV_MODE=true in a deployed env refuses startup
        instead of mounting an unauthenticated /v1/dev tree on a clinical service."""
        explicitly_local = self.environment == "local" and "environment" in self.model_fields_set
        if self.dev_mode and not explicitly_local:
            raise ValueError(
                "STRATA_DEV_MODE=true requires STRATA_ENVIRONMENT to be explicitly 'local'"
            )
        return self


settings = Settings()
