"""Settings, loaded from STRATA_-prefixed env vars. Observability knobs per."""

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STRATA_", env_file=".env", extra="ignore")

    app_name: str = "strata.booking"
    service_name: str = "strata.booking"
    environment: str = "local"

    # Database (required — the app fails fast at startup if unset; see .env.example).
    # Deliberately NO db_echo switch: statement echo logs bound parameters
    # member PII — so `personal`-classified services expose no such config path.
    database_url: str

    # The verification seam (strata.identity): cognito verifies pool
    # access tokens; dev verifies local HS256 tokens and only starts when
    # STRATA_ENVIRONMENT is EXPLICITLY "local" (core/security.py — the `environment`
    # default below does not count as explicit, so dev mode fails closed).
    auth_mode: Literal["cognito", "dev"] = "cognito"
    cognito_region: str | None = None
    cognito_user_pool_id: str | None = None
    cognito_client_id: str | None = None

    # Dev-mode seam (never enabled outside local): mounts /v1/dev/* and enables dev tokens.
    dev_mode: bool = False
    dev_auth_secret: str | None = None

    # CORS for the local Vite dev server (Summit); empty (off) by default.
    cors_origins: list[str] = []

    # Optional in-process jobs loop: run all due jobs every N seconds; 0 = off
    # (the default — jobs run via `inv jobs-run` or POST /v1/dev/jobs/run instead).
    jobs_interval_seconds: int = 0

    # Observability.
    log_format: Literal["console", "json"] = "console"
    otel_traces_exporter: Literal["none", "console", "otlp"] = "none"
    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_sampler_ratio: float = 1.0

    @model_validator(mode="after")
    def _dev_mode_requires_the_dev_verifier(self) -> "Settings":
        """Refuse the confusing mixed state at startup: the dev router mints HS256
        tokens only the dev verifier accepts, so mounting it alongside a cognito
        verifier yields mint-succeeds/auth-fails (or per-request 500s)."""
        if self.dev_mode and self.auth_mode != "dev":
            raise ValueError(
                "STRATA_DEV_MODE=true requires STRATA_AUTH_MODE=dev — the /v1/dev "
                "router mints dev tokens that only the dev verifier accepts"
            )
        return self


settings = Settings()
