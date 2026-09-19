"""Invoke tasks — the project's task runner (run as `.venv/bin/inv <task>`; `inv --list`).

Commands shell out to the project venv's own binaries (VENV prefix, mirroring
the sibling projects) so tasks work without activating the venv and never pick
up a different global tool version from PATH.

The db-* tasks target the SHARED workspace Postgres — the repo-root
docker-compose.yml, default port 5432 — the one container every
service and this project's tooling share.
"""

import shlex

from invoke import task
from invoke.context import Context

VENV = ".venv/bin"

SHARED_COMPOSE = "docker compose -f ../docker-compose.yml"


@task
def db_up(c: Context) -> None:
    """Start the shared workspace Postgres (root compose, port 5432); waits until healthy."""
    c.run(f"{SHARED_COMPOSE} up -d --wait db")


@task
def db_down(c: Context) -> None:
    """Stop the shared local Postgres (keeps data — the pgdata volume survives)."""
    c.run(f"{SHARED_COMPOSE} down")


@task
def db_reset(c: Context) -> None:
    """Wipe the shared local Postgres (drops the pgdata volume) and start it fresh."""
    c.run(f"{SHARED_COMPOSE} down -v")
    c.run(f"{SHARED_COMPOSE} up -d --wait db")


@task
def db_shell(c: Context) -> None:
    """Open psql against the shared local Postgres."""
    c.run(f"{SHARED_COMPOSE} exec db psql -U strata -d strata", pty=True)


@task
def migrate(c: Context) -> None:
    """Apply the canonical migration history to STRATA_DATABASE_URL."""
    c.run(f"{VENV}/alembic upgrade head")


@task
def migration(c: Context, name: str) -> None:
    """Autogenerate a new canonical migration (review it; must round-trip)."""
    c.run(f"{VENV}/alembic revision --autogenerate -m {shlex.quote(name)}")


@task
def domains_check(c: Context) -> None:
    """List any domain package on disk not wired into strata_core/domains/__init__.py."""
    c.run(f"{VENV}/python -m strata_core.domains_check")


@task
def classification_register(c: Context) -> None:
    """Regenerate DATA_CLASSIFICATION_REGISTER.md from the classification registry (drift-gated)."""
    c.run(f"{VENV}/python -m strata_core.classification_register")


@task
def seed(c: Context, profile: str = "demo") -> None:
    """Load a named fixture profile into STRATA_DATABASE_URL (deterministic, idempotent)."""
    c.run(f"{VENV}/python -m strata_core.fixtures --profile {shlex.quote(profile)}")


@task
def test(c: Context) -> None:
    """Run the test suite with coverage."""
    c.run(f"{VENV}/pytest --cov")


@task
def lint(c: Context) -> None:
    """Ruff lint + format check."""
    c.run(f"{VENV}/ruff check .")
    c.run(f"{VENV}/ruff format --check .")


@task
def typecheck(c: Context) -> None:
    """mypy (strict)."""
    c.run(f"{VENV}/mypy strata_core tests")


@task
def security(c: Context) -> None:
    """bandit (SAST) + pip-audit (dependency CVEs; the editable local dist is skipped)."""
    c.run(f"{VENV}/bandit -q -c pyproject.toml -r strata_core")
    c.run(f"{VENV}/pip-audit --skip-editable")


@task(pre=[lint, typecheck, security, test])
def check(c: Context) -> None:
    """Everything CI runs: lint + typecheck + security + tests."""
