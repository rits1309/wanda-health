"""Task runner (invoke). `inv --list` lists tasks; run via the venv (.venv/bin/inv <task>)."""

import shlex

from invoke import task

VENV = ".venv/bin"
OPS = f"{VENV}/python -m app.services.operations"


@task
def dev(c):
    """Run the API with autoreload at http://127.0.0.1:8004 (docs at /docs)."""
    # 8004 — 8000 is the engine/auth services' default.
    c.run(f"{VENV}/uvicorn app.main:app --reload --port 8004", pty=True)


@task(name="db-up")
def db_up(c):
    """Start the shared workspace Postgres (root compose, port 5432); waits until healthy."""
    c.run("docker compose -f ../docker-compose.yml up -d --wait db")


@task(name="db-down")
def db_down(c):
    """Stop the local Postgres."""
    c.run("docker compose -f ../docker-compose.yml down")


@task(name="pipeline-run")
def pipeline_run(c):
    """Run the pipeline consumer loop (dequeue → validate → resolve → hand to Worker)."""
    c.run(f"{VENV}/python -m app.services.consumer", pty=True)


@task
def replay(c, correlation_id):
    """Re-enqueue a captured payload under its original correlation ID."""
    c.run(f"{OPS} replay --correlation-id {shlex.quote(correlation_id)}")


@task
def undeliverable(c):
    """List undeliverable messages (retries exhausted) and possibly-stuck claims."""
    c.run(f"{OPS} undeliverable")


@task
def quarantine(c):
    """List open quarantine records — register the device, then `inv replay`."""
    c.run(f"{OPS} quarantine")


@task(name="quarantine-discard")
def quarantine_discard(c, correlation_id):
    """Close an open quarantine record as never-to-be-processed."""
    c.run(f"{OPS} quarantine-discard --correlation-id {shlex.quote(correlation_id)}")


@task
def migrate(c):
    """Apply the canonical migrations (strata.core owns the one history)."""
    c.run(
        f'{VENV}/python -c "from strata_core.migrations import upgrade_to_head; '
        "from app.config import settings; "
        'upgrade_to_head(settings.database_url)"'
    )


@task
def seed(c, profile="readings-demo"):
    """Load a named strata.core fixture profile (readings-demo: demo cast + registered
    devices, so the runbook's example payloads attribute out of the box)."""
    # strata.core's fixture CLI reads .env in the CWD — same STRATA_DATABASE_URL convention.
    c.run(f"{VENV}/python -m strata_core.fixtures --profile {shlex.quote(profile)}")


@task
def test(c):
    """Run the test suite with coverage."""
    c.run(f"{VENV}/python -m pytest --cov --cov-report=term-missing")


@task
def lint(c):
    """Ruff lint + format check."""
    c.run(f"{VENV}/ruff check .")
    c.run(f"{VENV}/ruff format --check .")


@task
def typecheck(c):
    """Static type check (mypy, strict)."""
    c.run(f"{VENV}/mypy app")


@task
def security(c):
    """SAST (bandit) + dependency CVE scan (pip-audit)."""
    c.run(f"{VENV}/bandit -q -c pyproject.toml -r app")
    c.run(f"{VENV}/pip-audit --skip-editable")


@task(pre=[lint, typecheck, security, test])
def check(c):
    """Everything CI runs, locally (lint + typecheck + security + tests)."""
