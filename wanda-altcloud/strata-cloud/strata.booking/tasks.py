"""Task runner (invoke). `inv --list` lists tasks; run via the venv (.venv/bin/inv <task>)."""

from invoke import task

VENV = ".venv/bin"


@task
def dev(c):
    """Run the API with autoreload at http://127.0.0.1:8010 (docs at /docs)."""
    c.run(f"{VENV}/uvicorn strata_booking.main:app --reload --port 8010", pty=True)


@task(name="db-up")
def db_up(c):
    """Start the shared workspace Postgres (root compose, port 5432); waits until healthy."""
    c.run("docker compose -f ../docker-compose.yml up -d --wait db")


@task(name="db-down")
def db_down(c):
    """Stop the local Postgres."""
    c.run("docker compose -f ../docker-compose.yml down")


@task(name="db-reset")
def db_reset(c):
    """Wipe and recreate the local Postgres (drops the data volume)."""
    c.run("docker compose -f ../docker-compose.yml down -v")
    c.run("docker compose -f ../docker-compose.yml up -d --wait db")


@task(name="db-shell")
def db_shell(c):
    """Open psql against the local Postgres."""
    c.run("docker compose -f ../docker-compose.yml exec db psql -U strata -d strata", pty=True)


@task
def migrate(c):
    """Apply the canonical migrations (strata.core owns the one history)."""
    c.run(
        f'{VENV}/python -c "from strata_core.migrations import upgrade_to_head; '
        "from strata_booking.core.config import settings; "
        'upgrade_to_head(settings.database_url)"'
    )


@task
def seed(c):
    """Load the deterministic local seed data (idempotent; needs db-up + migrate)."""
    c.run(f"{VENV}/python -m scripts.seed")


@task(name="jobs-run")
def jobs_run(c):
    """Run every due background job once (slot top-up + due reminders)."""
    c.run(f"{VENV}/python -m scripts.run_jobs")


@task
def token(c, role="coach", sub="c-1"):
    """Mint a dev bearer token: inv token --role coach --sub c-1.

    Sub + role only (the shared seam): programme membership and identity
    fields come from the kernel tables, never from token claims.
    """
    c.run(f"{VENV}/python -m scripts.mint_token --role {role} --sub {sub}")


@task(name="trace-up")
def trace_up(c):
    """Start the local Jaeger trace backend (UI at http://localhost:16686)."""
    c.run("docker compose -f ../docker-compose.yml --profile observability up -d jaeger")


@task(name="trace-down")
def trace_down(c):
    """Stop the local Jaeger."""
    c.run("docker compose -f ../docker-compose.yml --profile observability stop jaeger")


@task
def test(c):
    """Run the test suite with coverage (Docker required — ephemeral Postgres)."""
    c.run(f"{VENV}/python -m pytest --cov --cov-report=term-missing")


@task
def lint(c):
    """Ruff lint + format check."""
    c.run(f"{VENV}/ruff check .")
    c.run(f"{VENV}/ruff format --check .")


@task
def typecheck(c):
    """Static type check (mypy, strict)."""
    c.run(f"{VENV}/mypy strata_booking scripts")


@task
def security(c):
    """SAST (bandit) + dependency CVE scan (pip-audit)."""
    c.run(f"{VENV}/bandit -q -c pyproject.toml -r strata_booking scripts")
    c.run(f"{VENV}/pip-audit --skip-editable")


@task(pre=[lint, typecheck, security, test])
def check(c):
    """Everything CI runs, locally (lint + typecheck + security + tests)."""
