"""Application configuration, loaded from the environment (and an optional .env).

One typed ``Settings`` with the baseline ``STRATA_`` prefix — this retires the
old ``COGNITO_*`` names (see the README migration note).
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STRATA_", env_file=".env", extra="ignore")

    app_name: str = "Strata.Engine.Auth"

    # Cognito: the single platform user pool — see README for the dev pool.
    # Required, no defaults: a missing pool fails fast at startup.
    cognito_region: str
    cognito_user_pool_id: str
    cognito_client_id: str
    # Only set if the app client was created *with* a secret (confidential client).
    cognito_client_secret: str | None = None

    # The CREDENTIALED Cognito client (services/cognito.py `admin_cognito`) —
    # admin operations: the dev seed today, the migration login's
    # AdminCreateUser/AdminSetUserPassword/AdminDeleteUser at Identity.
    # Credentials come from the standard AWS chain, NEVER from Settings: a
    # developer session locally (`aws login`; name it here if it isn't your
    # default profile), an IAM role scoped to exactly those three actions when
    # deployed.
    aws_profile: str | None = None

    # The legacy platform's credentials-check endpoint — the migration login
    # (Identity). Unset → the migration branch is DISABLED and an
    # unknown user fails with the same generic error as a wrong password. UAT
    # needs no request auth (owner, 21-07-2026); production will require an API
    # key — set legacy_api_key then (env/secrets management, never a repo).
    legacy_api_base_url: str | None = None
    legacy_check_path: str = "/api/user/credentials/check/"
    legacy_api_key: str | None = None
    # Must sit well inside the sign-in time budget.
    legacy_timeout_seconds: float = 3.0

    # The pool password policy, MIRRORED locally (defaults = the Cognito defaults,
    # which the savanna-dev pool keeps — never relaxed for migration). The
    # migration login checks a legacy password against these BEFORE any Cognito
    # call so the upgrade-required signal costs nothing; the pool's own
    # InvalidPasswordException at set-password is the drift backstop (compensated,
    # logged as password_policy_drift). Change the pool ⇒ change these together.
    password_min_length: int = 8
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_digit: bool = True
    password_require_symbol: bool = True

    # The verification seam (strata.identity): cognito verifies pool access tokens;
    # dev verifies locally minted HS256 tokens and requires STRATA_ENVIRONMENT to be
    # EXPLICITLY set to "local" — the `environment` default below does not count
    # (core/auth.py passes None unless the env var was actually provided).
    auth_mode: Literal["cognito", "dev"] = "cognito"
    dev_auth_secret: str | None = None

    # Gates development-only surfaces (the seed script). Never true in a deployed
    # environment; the guards also require environment == "local".
    dev_mode: bool = False

    # The SHARED PostgreSQL — schema owned by ../strata.core's canonical migrations.
    # Required — a missing/misconfigured database fails fast at startup. Locally this
    # is the shared workspace Postgres (repo-root compose, port 5432, see .env.example).
    database_url: str
    db_echo: bool = False

    # Comma-separated browser origins allowed to call this API (CORS); defaults
    # cover the Vite/CRA dev servers Summit runs on.
    cors_allow_origins: str = "http://localhost:5173,http://localhost:3000"

    # Observability — identical knobs to the sibling services.
    service_name: str = "strata-engine-auth"
    # Observability label; the dev-auth guard ignores this default (it only
    # trusts an explicitly set STRATA_ENVIRONMENT — see core/auth.py).
    environment: str = "local"
    log_format: Literal["console", "json"] = "console"
    otel_traces_exporter: Literal["none", "console", "otlp"] = "none"
    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_sampler_ratio: float = 1.0


settings = Settings()  # values come from the env / .env (pydantic-settings)
