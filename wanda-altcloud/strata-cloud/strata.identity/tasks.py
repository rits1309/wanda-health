"""Invoke tasks — the project's task runner (run as `.venv/bin/inv <task>`; `inv --list`).

Commands shell out to the project venv's own binaries (VENV prefix, mirroring
strata.engine/tasks.py) so tasks work without activating the venv and never pick
up a different global tool version from PATH.
"""

from invoke import task
from invoke.context import Context

VENV = ".venv/bin"


@task
def db_up(c: Context) -> None:
    """Start the shared workspace Postgres (root compose, port 5432); waits until healthy."""
    c.run("docker compose -f ../docker-compose.yml up -d --wait db")


@task
def db_down(c: Context) -> None:
    """Stop the shared local Postgres (keeps data — the pgdata volume survives)."""
    c.run("docker compose -f ../docker-compose.yml down")


@task
def db_reset(c: Context) -> None:
    """Wipe the shared local Postgres (drops the pgdata volume) and start it fresh."""
    c.run("docker compose -f ../docker-compose.yml down -v")
    c.run("docker compose -f ../docker-compose.yml up -d --wait db")


@task
def db_shell(c: Context) -> None:
    """Open psql against the shared local Postgres."""
    c.run("docker compose -f ../docker-compose.yml exec db psql -U strata -d strata", pty=True)


@task
def migrate(c: Context) -> None:
    """Apply the canonical migrations (strata.core owns the one history)."""
    url = "postgresql+asyncpg://strata:strata@localhost:5432/strata"
    c.run(
        f'{VENV}/python -c "import os; from strata_core.migrations import upgrade_to_head; '
        f"upgrade_to_head(os.environ.get('STRATA_DATABASE_URL', '{url}'))\""
    )


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
    c.run(f"{VENV}/mypy strata_identity tests")


@task
def security(c: Context) -> None:
    """bandit (SAST) + pip-audit (dependency CVEs; the editable local dist is skipped)."""
    c.run(f"{VENV}/bandit -q -c pyproject.toml -r strata_identity")
    c.run(f"{VENV}/pip-audit --skip-editable")


@task(pre=[lint, typecheck, security, test])
def check(c: Context) -> None:
    """Everything CI runs: lint + typecheck + security + tests."""
