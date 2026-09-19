"""Settings, loaded from STRATA_-prefixed env vars. Observability knobs per."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STRATA_", env_file=".env", extra="ignore")

    app_name: str = "strata.terminology"
    service_name: str = "strata.terminology"
    environment: str = "local"

    # Max number of results returned from a single search, per reference domain.
    medication_search_limit: int = 20
    procedure_search_limit: int = 20
    diagnosis_search_limit: int = 20

    # PostgreSQL connection (async). Required — no default, so a missing/misconfigured
    # database fails fast at startup rather than silently degrading. Locally this points at
    # the shared workspace Postgres (repo-root compose, port 5432 — see .env.example); in
    # tests the testcontainers fixture sets it; on AWS it is injected from Secrets Manager.
    database_url: str
    # No SQL statement echo — deliberately NO db_echo knob: although this
    # service is classified `internal`, bound parameters carry coach identity
    # + typed search terms, and the platform posture is uniform no-echo. Never
    # reintroduce a configuration path that logs bound parameters.

    # The verification seam (strata.identity): cognito verifies pool
    # access tokens; dev verifies local HS256 tokens and only starts when
    # STRATA_ENVIRONMENT is EXPLICITLY "local" (core/security.py — the `environment`
    # default above does not count as explicit, so dev mode fails closed).
    auth_mode: Literal["cognito", "dev"] = "cognito"
    cognito_region: str | None = None
    cognito_user_pool_id: str | None = None
    cognito_client_id: str | None = None
    dev_auth_secret: str | None = None

    # CORS for the local Vite dev server (Summit); empty (off) by default.
    cors_origins: list[str] = []

    # Observability.
    log_format: Literal["console", "json"] = "console"
    otel_traces_exporter: Literal["none", "console", "otlp"] = "none"
    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_sampler_ratio: float = 1.0


settings = Settings()
